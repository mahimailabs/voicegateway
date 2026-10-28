package api

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/mail"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// AlertStore keeps alert setup, incidents and deliveries (docs/specs/alerts-delivery.md). web writes
// rule and channel setup, test deliveries and a person's resolve; the worker writes the rest.
type AlertStore interface {
	AlertRules(ctx context.Context, organisationID, projectID string) ([]store.AlertRule, error)
	PutAlertRule(ctx context.Context, organisationID, projectID string, r store.AlertRule) (store.AlertRule, error)
	AlertChannels(ctx context.Context, organisationID, projectID string) ([]store.AlertChannel, error)
	AlertChannelConfig(ctx context.Context, organisationID, projectID, kind string) ([]byte, error)
	PutAlertChannel(ctx context.Context, organisationID, projectID, kind string, config []byte, target string) error
	DeleteAlertChannel(ctx context.Context, organisationID, projectID, kind string) error
	QueueAlertTest(ctx context.Context, organisationID, projectID, kind, key string, message json.RawMessage) error
	AlertIncidents(ctx context.Context, organisationID, projectID string, from, to time.Time, limit int) ([]store.AlertIncident, map[string][]store.AlertDeliveryStatus, error)
	ResolveAlertIncidentByPerson(ctx context.Context, organisationID, projectID, incidentID, userID string, now time.Time) (store.AlertIncident, error)
	AlertEvents(ctx context.Context, organisationID, projectID string, from, to time.Time, limit int) ([]store.AlertEvent, int, error)
}

// Limits of the alert routes.
const (
	maxAlertBody      = 8 << 10
	maxRecipients     = 20
	maxWebhookURL     = 2048
	maxAlertIncidents = 500
	maxAlertEvents    = 5000
)

const codeIncidentNotResolvable = "incident_not_resolvable"

// alertSetup is the contract's AlertSetup.
type alertSetup struct {
	Items          []store.AlertRule `json:"items"`
	Channels       []alertChannel    `json:"channels"`
	EmailAvailable bool              `json:"emailAvailable"`
}

// alertChannel is the contract's AlertChannel: what a channel sends to, never its URL or secret.
type alertChannel struct {
	Kind         string         `json:"kind"`
	Target       string         `json:"target"`
	Recipients   *[]string      `json:"recipients"`
	Broken       bool           `json:"broken"`
	UpdatedAt    time.Time      `json:"updatedAt"`
	LastDelivery *alertDelivery `json:"lastDelivery"`
}

// alertDelivery is the contract's AlertDelivery.
type alertDelivery struct {
	ID            string     `json:"id"`
	Channel       string     `json:"channel"`
	Change        string     `json:"change"`
	State         string     `json:"state"`
	Attempts      int        `json:"attempts"`
	NextAttemptAt *time.Time `json:"nextAttemptAt"`
	Status        *string    `json:"status"`
	Message       *string    `json:"message"`
	DeliveredAt   *time.Time `json:"deliveredAt"`
	CreatedAt     time.Time  `json:"createdAt"`
}

func toAlertDelivery(d store.AlertDeliveryStatus) alertDelivery {
	out := alertDelivery{
		ID: d.ID, Channel: d.Channel, Change: d.Change, State: d.State, Attempts: d.Attempts,
		Status: d.LastStatus, Message: d.LastError, DeliveredAt: d.DeliveredAt, CreatedAt: d.CreatedAt,
	}
	// A next attempt is a time only while one is planned.
	if d.State == "pending" || d.State == "retrying" {
		out.NextAttemptAt = d.NextAttemptAt
	}
	return out
}

// alertIncident is the contract's AlertIncident.
type alertIncident struct {
	ID             string          `json:"id"`
	RuleID         string          `json:"ruleId"`
	Subject        string          `json:"subject"`
	State          string          `json:"state"`
	OpenedAt       time.Time       `json:"openedAt"`
	ResolvedAt     *time.Time      `json:"resolvedAt"`
	ResolvedBy     *string         `json:"resolvedBy"`
	Occurrences    int             `json:"occurrences"`
	LastOccurrence alertOccurrence `json:"lastOccurrence"`
	HoldReason     *string         `json:"holdReason"`
	SilentSince    *time.Time      `json:"silentSince"`
	Deliveries     []alertDelivery `json:"deliveries"`
}

type alertOccurrence struct {
	At     *time.Time `json:"at"`
	Where  string     `json:"where"`
	Detail string     `json:"detail"`
	CallID *string    `json:"callId"`
}

func toAlertIncident(inc store.AlertIncident, deliveries []store.AlertDeliveryStatus) alertIncident {
	out := alertIncident{
		ID: inc.ID, RuleID: inc.RuleID, Subject: inc.SubjectLabel, State: inc.State, OpenedAt: inc.OpenedAt,
		ResolvedAt: inc.ResolvedAt, Occurrences: inc.Occurrences, HoldReason: inc.HoldReason, SilentSince: inc.SilentSince,
		LastOccurrence: alertOccurrence{At: inc.LastOccurrenceAt, Where: inc.Last.Where, Detail: inc.Last.Detail, CallID: inc.Last.CallID},
		Deliveries:     make([]alertDelivery, 0, len(deliveries)),
	}
	if inc.ResolvedBy != nil {
		by := "person"
		if *inc.ResolvedBy == "condition" {
			by = "condition"
		}
		out.ResolvedBy = &by
	}
	for _, d := range deliveries {
		out.Deliveries = append(out.Deliveries, toAlertDelivery(d))
	}
	return out
}

// alertsReady answers 503 when the alert store is not wired, and 404 for an unknown project.
func (a *API) alertsReady(w http.ResponseWriter, r *http.Request) (org, proj string, ok bool) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Alerts == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "alerts unavailable")
		return "", "", false
	}
	org, proj = chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(r.Context(), org, proj); err != nil {
		a.fail(w, r, err, "project not found")
		return "", "", false
	}
	return org, proj, true
}

// getAlertSetup serves operationId getAlertSetup: the seven rules' setup and the configured channels.
func (a *API) getAlertSetup(w http.ResponseWriter, r *http.Request) {
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	rules, err := a.Alerts.AlertRules(r.Context(), org, proj)
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	channels, err := a.Alerts.AlertChannels(r.Context(), org, proj)
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	out := alertSetup{Items: rules, Channels: make([]alertChannel, 0, len(channels)), EmailAvailable: a.AlertEmail}
	for _, c := range channels {
		ch := alertChannel{Kind: c.Kind, Target: c.Target, Broken: c.Broken, UpdatedAt: c.UpdatedAt}
		if c.Last != nil {
			d := toAlertDelivery(*c.Last)
			ch.LastDelivery = &d
		}
		if c.Kind == "email" {
			ch.Recipients = a.emailRecipients(r.Context(), org, proj)
		}
		out.Channels = append(out.Channels, ch)
	}
	writeJSON(w, http.StatusOK, out)
}

// emailRecipients opens an email channel's recipients; nil when they cannot be read.
func (a *API) emailRecipients(ctx context.Context, org, proj string) *[]string {
	raw, err := a.Alerts.AlertChannelConfig(ctx, org, proj, "email")
	if err != nil {
		return nil
	}
	var c store.AlertEmailConfig
	if json.Unmarshal(raw, &c) != nil {
		return nil
	}
	return &c.Recipients
}

// decodeAlertBody reads one JSON object of at most maxAlertBody into v, refusing unknown fields.
func decodeAlertBody(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxAlertBody))
	dec.DisallowUnknownFields()
	var trailing any
	if dec.Decode(v) != nil || !errors.Is(dec.Decode(&trailing), io.EOF) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected one JSON object, at most 8 KiB")
		return false
	}
	return true
}

// updateAlertRule serves operationId updateAlertRule: whether a rule sends, and to which kinds.
func (a *API) updateAlertRule(w http.ResponseWriter, r *http.Request) {
	ruleID := chi.URLParam(r, "ruleId")
	if !slices.Contains(store.AlertRuleIDs, ruleID) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such alert rule")
		return
	}
	var body struct {
		On *bool    `json:"on"`
		To []string `json:"to"`
	}
	if !decodeAlertBody(w, r, &body) {
		return
	}
	if body.On == nil || body.To == nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "on and to are required")
		return
	}
	to := []string{}
	for _, kind := range body.To {
		if !slices.Contains(store.AlertChannelKinds, kind) {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "to may name email and webhook; other channels are not available yet")
			return
		}
		if !slices.Contains(to, kind) {
			to = append(to, kind)
		}
	}
	slices.Sort(to)
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	saved, err := a.Alerts.PutAlertRule(r.Context(), org, proj, store.AlertRule{RuleID: ruleID, On: *body.On, To: to})
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	writeJSON(w, http.StatusOK, saved)
}

// savedAlertChannel is the contract's SavedAlertChannel: the signing secret is here only, once,
// when it was made.
type savedAlertChannel struct {
	Kind          string    `json:"kind"`
	Target        string    `json:"target"`
	Recipients    *[]string `json:"recipients"`
	SigningSecret *string   `json:"signingSecret"`
}

// updateAlertChannel serves operationId updateAlertChannel: an Admin sets where a channel sends.
// A webhook keeps its signing secret unless rotateSecret asks for a new one; a new channel always
// gets one.
func (a *API) updateAlertChannel(w http.ResponseWriter, r *http.Request) {
	kind := chi.URLParam(r, "kind")
	if !slices.Contains(store.AlertChannelKinds, kind) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such alert channel; email and webhook are available")
		return
	}
	var body struct {
		URL          *string  `json:"url"`
		RotateSecret bool     `json:"rotateSecret"`
		Recipients   []string `json:"recipients"`
	}
	if !decodeAlertBody(w, r, &body) {
		return
	}
	var config []byte
	var out savedAlertChannel
	var err error
	switch kind {
	case "email":
		if !a.AlertEmail {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "this server sends no email: set VOICEGATEWAY_SMTP_URL first")
			return
		}
		recipients, ok := emailList(body.Recipients)
		if body.URL != nil || body.RotateSecret || !ok {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "recipients must be 1 to 20 email addresses")
			return
		}
		config, err = json.Marshal(store.AlertEmailConfig{Recipients: recipients})
		out = savedAlertChannel{Kind: kind, Target: recipientsTarget(len(recipients)), Recipients: &recipients}
	case "webhook":
		host, ok := webhookHost(body.URL)
		if body.Recipients != nil || !ok {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "url must be an http or https URL with a host, at most 2048 characters")
			return
		}
		org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
		secret := ""
		if !body.RotateSecret && a.Alerts != nil {
			if raw, err := a.Alerts.AlertChannelConfig(r.Context(), org, proj, "webhook"); err == nil {
				var prior store.AlertWebhookConfig
				if json.Unmarshal(raw, &prior) == nil {
					secret = prior.Secret
				}
			}
		}
		out = savedAlertChannel{Kind: kind, Target: host}
		if secret == "" {
			if secret, err = newSigningSecret(); err != nil {
				a.fail(w, r, err, "")
				return
			}
			out.SigningSecret = &secret
		}
		// Sealed by the store before it is written; never sent back.
		config, err = json.Marshal(store.AlertWebhookConfig{URL: *body.URL, Secret: secret}) //nolint:gosec // G117: sealed configuration, not an answer
	}
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	if err := a.Alerts.PutAlertChannel(r.Context(), org, proj, kind, config, out.Target); err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	writeJSON(w, http.StatusOK, out)
}

// emailList trims, checks and de-duplicates recipients: 1 to 20 bare addresses.
func emailList(in []string) ([]string, bool) {
	out := []string{}
	for _, s := range in {
		s = strings.TrimSpace(s)
		addr, err := mail.ParseAddress(s)
		if err != nil || addr.Address != s || addr.Name != "" {
			return nil, false
		}
		if !slices.ContainsFunc(out, func(o string) bool { return strings.EqualFold(o, s) }) {
			out = append(out, s)
		}
	}
	return out, len(out) > 0 && len(out) <= maxRecipients
}

func recipientsTarget(n int) string {
	if n == 1 {
		return "1 recipient"
	}
	return strconv.Itoa(n) + " recipients"
}

// webhookHost checks a webhook URL and returns its host, the only part the console shows: a path
// or query may carry a token.
func webhookHost(raw *string) (string, bool) {
	if raw == nil || len(*raw) > maxWebhookURL {
		return "", false
	}
	u, err := url.Parse(*raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.Fragment != "" {
		return "", false
	}
	return u.Hostname(), true
}

// newSigningSecret draws a Standard Webhooks signing secret: whsec_ and 32 random bytes in base64.
func newSigningSecret() (string, error) {
	var b [32]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return "whsec_" + base64.StdEncoding.EncodeToString(b[:]), nil
}

// deleteAlertChannel serves operationId deleteAlertChannel.
func (a *API) deleteAlertChannel(w http.ResponseWriter, r *http.Request) {
	kind := chi.URLParam(r, "kind")
	if !slices.Contains(store.AlertChannelKinds, kind) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such alert channel")
		return
	}
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	if err := a.Alerts.DeleteAlertChannel(r.Context(), org, proj, kind); err != nil {
		a.fail(w, r, err, "the channel is not configured")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// testAlertChannel serves operationId testAlertChannel: queues one test delivery, recorded like any
// other.
func (a *API) testAlertChannel(w http.ResponseWriter, r *http.Request) {
	kind := chi.URLParam(r, "kind")
	if !slices.Contains(store.AlertChannelKinds, kind) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such alert channel")
		return
	}
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	if a.AlertTest == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "alert tests unavailable")
		return
	}
	msg, err := a.AlertTest(org, proj)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	var nonce [9]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		a.fail(w, r, err, "")
		return
	}
	key := "test/" + base64.RawURLEncoding.EncodeToString(nonce[:]) + "/" + kind
	err = a.Alerts.QueueAlertTest(r.Context(), org, proj, kind, key, msg)
	if errors.Is(err, store.ErrAlertChannelMissing) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "the channel is not configured")
		return
	}
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	// 204, not 202: the delivery is queued, and the console reads its outcome from lastDelivery.
	w.WriteHeader(http.StatusNoContent)
}

// alertRange reads the from and to query parameters.
func alertRange(w http.ResponseWriter, r *http.Request) (from, to time.Time, ok bool) {
	q := r.URL.Query()
	from, err := time.Parse(time.RFC3339Nano, q.Get("from"))
	if err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "from must be an RFC 3339 date-time")
		return from, to, false
	}
	to, err = time.Parse(time.RFC3339Nano, q.Get("to"))
	if err != nil || !from.Before(to) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "to must be an RFC 3339 date-time after from")
		return from, to, false
	}
	return from.UTC(), to.UTC(), true
}

// listAlertIncidents serves operationId listAlertIncidents: incidents open in or across a range, newest
// first, each with its deliveries.
func (a *API) listAlertIncidents(w http.ResponseWriter, r *http.Request) {
	from, to, ok := alertRange(w, r)
	if !ok {
		return
	}
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	incidents, deliveries, err := a.Alerts.AlertIncidents(r.Context(), org, proj, from, to, maxAlertIncidents+1)
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	out := struct {
		From     time.Time       `json:"from"`
		To       time.Time       `json:"to"`
		Complete bool            `json:"complete"`
		Items    []alertIncident `json:"items"`
	}{From: from, To: to, Complete: len(incidents) <= maxAlertIncidents, Items: []alertIncident{}}
	for _, inc := range incidents[:min(len(incidents), maxAlertIncidents)] {
		out.Items = append(out.Items, toAlertIncident(inc, deliveries[inc.ID]))
	}
	writeJSON(w, http.StatusOK, out)
}

// resolveAlertIncident serves operationId resolveAlertIncident: a person resolves a firing
// recording-lost incident, which no condition can (the recording is gone).
func (a *API) resolveAlertIncident(w http.ResponseWriter, r *http.Request) {
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	p, _ := store.PrincipalFrom(r.Context())
	by := p.UserID
	if by == "" {
		by = "key"
	}
	inc, err := a.Alerts.ResolveAlertIncidentByPerson(r.Context(), org, proj, chi.URLParam(r, "incidentId"), by, a.Now())
	if errors.Is(err, store.ErrAlertIncidentState) {
		writeError(w, r, http.StatusConflict, codeIncidentNotResolvable, "only a firing recording-lost incident is resolved by a person; the others resolve when their condition holds")
		return
	}
	if err != nil {
		a.fail(w, r, err, "no such incident")
		return
	}
	writeJSON(w, http.StatusOK, toAlertIncident(inc, nil))
}

// alertEvents is the contract's AlertEvents.
type alertEvents struct {
	From     time.Time         `json:"from"`
	To       time.Time         `json:"to"`
	Complete bool              `json:"complete"`
	Rules    []alertEventsRule `json:"rules"`
}

type alertEventsRule struct {
	RuleID string            `json:"ruleId"`
	State  *string           `json:"state"`
	Events []alertOccurrence `json:"events"`
}

// listAlertEvents serves operationId listAlertEvents: what each rule matched in a range, as the
// Issues page stored it. This is evaluation, not delivery history.
func (a *API) listAlertEvents(w http.ResponseWriter, r *http.Request) {
	from, to, ok := alertRange(w, r)
	if !ok {
		return
	}
	org, proj, ok := a.alertsReady(w, r)
	if !ok {
		return
	}
	events, trunks, err := a.Alerts.AlertEvents(r.Context(), org, proj, from, to, maxAlertEvents+1)
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	out := alertEvents{From: from, To: to, Complete: len(events) <= maxAlertEvents, Rules: make([]alertEventsRule, 0, len(store.AlertRuleIDs))}
	for _, id := range store.AlertRuleIDs {
		rule := alertEventsRule{RuleID: id, Events: []alertOccurrence{}}
		if id == "plain-phone" && trunks > 0 {
			state := strconv.Itoa(trunks) + " trunks now"
			if trunks == 1 {
				state = "1 trunk now"
			}
			rule.State = &state
		}
		out.Rules = append(out.Rules, rule)
	}
	for _, e := range events[:min(len(events), maxAlertEvents)] {
		if i := slices.Index(store.AlertRuleIDs, e.RuleID); i >= 0 {
			out.Rules[i].Events = append(out.Rules[i].Events, alertOccurrence{At: e.At, Where: e.Where, Detail: e.Detail, CallID: e.CallID})
		}
	}
	writeJSON(w, http.StatusOK, out)
}
