package alert

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"

	standardwebhooks "github.com/standard-webhooks/standard-webhooks/libraries/go"

	"github.com/mahimailabs/voicegateway/internal/mailer"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// Retry schedule (alerts-behaviour.md, Delivery): 30 s, then doubling up to 30 min, for 24 hours.
const (
	firstRetry = 30 * time.Second
	maxRetry   = 30 * time.Minute
	retryFor   = 24 * time.Hour
	// lease is how long a claimed delivery stays with one worker before another may take it.
	lease = 2 * time.Minute
	// sendTimeout bounds one attempt.
	sendTimeout = 10 * time.Second
	// claimBatch caps the deliveries one tick sends: all of them, one after another, finish inside
	// TickBudget, well inside the lease, so no other worker claims one still being sent.
	claimBatch = 5
	// finishTimeout bounds recording one attempt, even after the tick's context ends.
	finishTimeout = 5 * time.Second
	// errorBytes caps the provider message kept from a refusal.
	errorBytes = 200
)

// TickBudget is how long one Tick may run: every claimed delivery sent and recorded, 75 s, inside
// the 2 minute lease.
const TickBudget = claimBatch * (sendTimeout + finishTimeout)

// Deliverer sends due deliveries. HTTP dials through the worker's address guard
// (scrape.NewWorkerClient), whose refusals Refused recognises; Mailer is nil without
// VOICEGATEWAY_SMTP_URL.
type Deliverer struct {
	Store  Deliveries
	HTTP   *http.Client
	Mailer Mailer
	Log    *slog.Logger
	Now    func() time.Time
	// Refused tells an address the HTTP client's guard refuses from a network failure (scrape.Refused).
	Refused func(error) bool
}

// outcome is one attempt's result before the retry schedule applies.
type outcome struct {
	status string
	err    *string
	// retryAfter is the server's Retry-After, zero when it gave none.
	retryAfter time.Duration
	retry      bool
	ok         bool
}

// Tick claims due deliveries and sends each once.
func (d *Deliverer) Tick(ctx context.Context, now time.Time) error {
	due, err := d.Store.ClaimAlertDeliveries(ctx, now, now.Add(lease), claimBatch)
	if err != nil {
		return err
	}
	var errs []error
	for _, del := range due {
		// A delivery not sent before the tick ends keeps its lease and goes out once it lapses.
		if err := ctx.Err(); err != nil {
			errs = append(errs, err)
			break
		}
		o := d.send(ctx, del)
		a := d.schedule(del, o, d.Now())
		// Recorded even when the tick's context ended during the send, so an answer the channel
		// already gave is not sent again.
		fctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), finishTimeout)
		err := d.Store.FinishAlertAttempt(fctx, del, a)
		cancel()
		if err != nil {
			errs = append(errs, fmt.Errorf("alert delivery %s: %w", del.ID, err))
			continue
		}
		if a.State != "delivered" {
			d.Log.Warn("alert delivery not delivered", "delivery", del.ID, "channel", del.Channel, "state", a.State, "status", a.Status)
		}
	}
	return errors.Join(errs...)
}

// schedule turns an attempt's outcome into the delivery's next state.
func (d *Deliverer) schedule(del store.AlertDelivery, o outcome, now time.Time) store.AlertAttempt {
	a := store.AlertAttempt{At: now, Status: o.status, Error: o.err, NextAttemptAt: now}
	switch {
	case o.ok:
		a.State = "delivered"
	case o.retry && now.Sub(del.CreatedAt) < retryFor:
		a.State = "retrying"
		a.NextAttemptAt = now.Add(max(backoff(del.Attempts+1), min(o.retryAfter, retryFor)))
	default:
		a.State = "failed"
		a.Broken = !o.retry
		a.Issue = issueText(del, o)
	}
	return a
}

// backoff is the wait after the nth failed attempt: 30 s, 1 min, 2 min, doubling to 30 min.
func backoff(n int) time.Duration {
	wait := firstRetry
	for i := 1; i < n && wait < maxRetry; i++ {
		wait *= 2
	}
	return min(wait, maxRetry)
}

var channelNames = map[string]string{"email": "Email", "webhook": "Webhook"}

// issueText says on the Issues page what was not delivered, where, and why.
func issueText(del store.AlertDelivery, o outcome) string {
	what := "Test alert"
	var m Message
	if json.Unmarshal(del.Message, &m) == nil && m.Incident != nil {
		what = strings.TrimPrefix(m.Type, "alert.") + " of " + m.Incident.Title
		what = strings.ToUpper(what[:1]) + what[1:]
	}
	why := o.status
	if o.err != nil {
		why += ", " + *o.err
	}
	return fmt.Sprintf("%s not delivered to %s: %s", what, channelNames[del.Channel], why)
}

func (d *Deliverer) send(ctx context.Context, del store.AlertDelivery) outcome {
	config, err := d.Store.AlertChannelConfig(ctx, del.OrganisationID, del.ProjectID, del.Channel)
	if errors.Is(err, store.ErrAlertChannelMissing) {
		return refused("channel_removed", "the channel was removed before delivery")
	}
	if err != nil {
		// The store failing is ours, not the channel's: try again later.
		return outcome{status: "internal", err: ptr("could not read the channel"), retry: true}
	}
	ctx, cancel := context.WithTimeout(ctx, sendTimeout)
	defer cancel()
	switch del.Channel {
	case "webhook":
		var c store.AlertWebhookConfig
		if err := json.Unmarshal(config, &c); err != nil {
			return refused("bad_config", "the channel configuration could not be read")
		}
		return d.webhook(ctx, c, del)
	case "email":
		var c store.AlertEmailConfig
		if err := json.Unmarshal(config, &c); err != nil {
			return refused("bad_config", "the channel configuration could not be read")
		}
		return d.email(ctx, c, del)
	}
	return refused("bad_config", "unknown channel kind")
}

// webhook POSTs the message, signed per Standard Webhooks: webhook-id is the delivery's idempotency
// key, the same on every retry, and Idempotency-Key repeats it for receivers that read that name.
func (d *Deliverer) webhook(ctx context.Context, c store.AlertWebhookConfig, del store.AlertDelivery) outcome {
	signer, err := standardwebhooks.NewWebhook(c.Secret)
	if err != nil {
		return refused("bad_config", "the signing secret could not be read")
	}
	at := d.Now()
	signature, err := signer.Sign(del.Key, at, del.Message)
	if err != nil {
		return refused("bad_config", "the message could not be signed")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.URL, bytes.NewReader(del.Message))
	if err != nil {
		return refused("bad_config", "the webhook URL could not be used")
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "VoiceGateway-Alerts")
	req.Header.Set(standardwebhooks.HeaderWebhookID, del.Key)
	req.Header.Set(standardwebhooks.HeaderWebhookTimestamp, strconv.FormatInt(at.Unix(), 10))
	req.Header.Set(standardwebhooks.HeaderWebhookSignature, signature)
	req.Header.Set("Idempotency-Key", del.Key)
	resp, err := d.HTTP.Do(req)
	if err != nil {
		// The error text repeats the URL, which may carry a token: keep its class only.
		if errors.Is(err, context.DeadlineExceeded) {
			return outcome{status: "timeout", err: ptr("no answer within 10 s"), retry: true}
		}
		// The worker's address guard refused the host's address: the same on every retry.
		if d.Refused != nil && d.Refused(err) {
			return refused("address_not_allowed", "the address is not allowed from this server")
		}
		return outcome{status: "unreachable", err: ptr("could not connect"), retry: true}
	}
	defer func() { _ = resp.Body.Close() }()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
	o := outcome{status: "HTTP " + strconv.Itoa(resp.StatusCode)}
	if msg := providerMessage(body, c); msg != "" {
		o.err = &msg
	}
	switch code := resp.StatusCode; {
	case code >= 200 && code < 300:
		o.ok, o.err = true, nil
	case code == http.StatusTooManyRequests || code == http.StatusRequestTimeout || code >= 500:
		o.retry = true
		o.retryAfter = retryAfter(resp.Header.Get("Retry-After"), d.Now())
	}
	// Any other answer, redirects included (never followed), marks the channel broken.
	return o
}

// email sends the message to every recipient as one email.
func (d *Deliverer) email(ctx context.Context, c store.AlertEmailConfig, del store.AlertDelivery) outcome {
	if d.Mailer == nil {
		return refused("smtp_not_configured", "this server has no VOICEGATEWAY_SMTP_URL")
	}
	var m Message
	if err := json.Unmarshal(del.Message, &m); err != nil || len(c.Recipients) == 0 {
		return refused("bad_config", "the message or recipients could not be read")
	}
	subject, body := email(m)
	err := d.Mailer.SendTo(ctx, c.Recipients, oneLine(subject), body)
	if err == nil {
		return outcome{status: "sent", ok: true}
	}
	var me *mailer.Error
	if errors.As(err, &me) {
		// An unknown final acknowledgement may have been accepted: retry, at least once.
		return outcome{status: "smtp " + me.Class(), err: ptr("the mail server did not accept the message"), retry: me.Temporary() || me.DeliveryUnknown()}
	}
	return outcome{status: "smtp", err: ptr("the mail server could not be used"), retry: true}
}

func refused(status, msg string) outcome { return outcome{status: status, err: &msg} }

func ptr[T any](v T) *T { return &v }

// retryAfter reads a Retry-After header, in seconds or as an HTTP date; zero when absent or bad.
func retryAfter(v string, now time.Time) time.Duration {
	if v == "" {
		return 0
	}
	if s, err := strconv.Atoi(v); err == nil && s > 0 {
		return time.Duration(s) * time.Second
	}
	if t, err := http.ParseTime(v); err == nil && t.After(now) {
		return t.Sub(now)
	}
	return 0
}

// providerMessage keeps a short, printable part of a refusal's body, with the channel's URL, every
// part of its path, query and user info, and its secret taken out, so a body that echoes a token
// never reaches the console.
func providerMessage(body []byte, c store.AlertWebhookConfig) string {
	s := string(body)
	secrets := []string{c.URL, c.Secret}
	if u, err := url.Parse(c.URL); err == nil {
		secrets = append(secrets, u.RawQuery, u.User.String())
		secrets = append(secrets, strings.Split(u.Path, "/")...)
		for _, values := range u.Query() {
			secrets = append(secrets, values...)
		}
	}
	// Longest first, so a part never splits a longer secret before it is removed.
	slices.SortFunc(secrets, func(a, b string) int { return len(b) - len(a) })
	for _, secret := range secrets {
		if len(secret) >= 4 {
			s = strings.ReplaceAll(s, secret, "[redacted]")
		}
	}
	s = strings.Map(func(r rune) rune {
		if unicode.IsPrint(r) {
			return r
		}
		return ' '
	}, s)
	s = strings.Join(strings.Fields(s), " ")
	if len(s) > errorBytes {
		s = strings.ToValidUTF8(s[:errorBytes], "") + "..."
	}
	return s
}

// oneLine keeps a header value on one line.
func oneLine(s string) string { return strings.Join(strings.Fields(s), " ") }
