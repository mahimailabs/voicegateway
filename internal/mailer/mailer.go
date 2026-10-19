// Package mailer sends plain-text email over the one SMTP setting, VOICEGATEWAY_SMTP_URL, with
// github.com/wneessen/go-mail (phase 06d). Invitations use it now; the alerts email channel will
// use the same setting.
package mailer

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/wneessen/go-mail"
)

// timeout bounds one dial and send, so a dead server cannot hold a request.
const timeout = 10 * time.Second

// Mailer sends through one SMTP server. Its fields hold the server's password; it is never
// printed or logged.
type Mailer struct {
	host string
	opts []mail.Option
	from string
}

// New reads an SMTP URL: smtps://user:pass@host:465 for implicit TLS, or smtp://user:pass@host:587,
// which requires STARTTLS. Plain SMTP, without TLS, is allowed only to a loopback host. from is
// the sender's address. Errors never repeat the URL, which holds the password.
func New(rawURL, from string) (*Mailer, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, errors.New("VOICEGATEWAY_SMTP_URL is not a URL")
	}
	if u.Hostname() == "" {
		return nil, errors.New("VOICEGATEWAY_SMTP_URL names no host")
	}
	if from == "" {
		return nil, errors.New("VOICEGATEWAY_SMTP_FROM is required with VOICEGATEWAY_SMTP_URL")
	}
	if err := mail.NewMsg().From(from); err != nil {
		return nil, errors.New("VOICEGATEWAY_SMTP_FROM is not an email address")
	}
	opts := []mail.Option{mail.WithTimeout(timeout)}
	port := 0
	switch strings.ToLower(u.Scheme) {
	case "smtps":
		opts, port = append(opts, mail.WithSSL()), 465
	case "smtp":
		port = 587
		if loopback(u.Hostname()) {
			opts = append(opts, mail.WithTLSPolicy(mail.TLSOpportunistic))
		} else {
			opts = append(opts, mail.WithTLSPolicy(mail.TLSMandatory))
		}
	default:
		return nil, errors.New("VOICEGATEWAY_SMTP_URL must start with smtps:// or smtp://")
	}
	if p := u.Port(); p != "" {
		if port, err = strconv.Atoi(p); err != nil || port < 1 || port > 65535 {
			return nil, errors.New("VOICEGATEWAY_SMTP_URL has a bad port")
		}
	}
	opts = append(opts, mail.WithPort(port))
	if user := u.User.Username(); user != "" {
		pass, _ := u.User.Password()
		opts = append(opts, mail.WithSMTPAuth(mail.SMTPAuthAutoDiscover), mail.WithUsername(user), mail.WithPassword(pass))
	}
	if _, err := mail.NewClient(u.Hostname(), opts...); err != nil {
		return nil, errors.New("VOICEGATEWAY_SMTP_URL could not be used")
	}
	return &Mailer{host: u.Hostname(), opts: opts, from: from}, nil
}

// loopback reports whether host is localhost or a loopback address.
func loopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	a, err := netip.ParseAddr(host)
	return err == nil && a.IsLoopback()
}

// Send emails one plain-text message. A failure is an *Error naming its class.
func (m *Mailer) Send(ctx context.Context, to, subject, body string) error {
	msg := mail.NewMsg()
	if err := msg.From(m.from); err != nil {
		return &Error{class: "rejected", err: err}
	}
	if err := msg.To(to); err != nil {
		return &Error{class: "rejected", err: err}
	}
	msg.Subject(subject)
	msg.SetBodyString(mail.TypeTextPlain, body)
	c, err := mail.NewClient(m.host, m.opts...)
	if err != nil {
		return &Error{class: "unreachable", err: err}
	}
	if err := c.DialAndSendWithContext(ctx, msg); err != nil && !msg.IsDelivered() {
		return &Error{class: classify(err), err: err}
	}
	return nil
}

// Error is a failed send. Class is unreachable, tls, auth or rejected; the wrapped error's text may
// quote the server, so callers log the class only.
type Error struct {
	class string
	err   error
}

func (e *Error) Error() string { return fmt.Sprintf("smtp: %s", e.class) }
func (e *Error) Unwrap() error { return e.err }

// DeliveryUnknown reports a missing final DATA acknowledgement. A transport failure
// here does not prove rejection: the server may already have accepted the message.
func (e *Error) DeliveryUnknown() bool {
	var se *mail.SendError
	return errors.As(e.err, &se) && (se.Reason == mail.ErrAmbiguous ||
		(se.Reason == mail.ErrSMTPDataClose && se.ErrorCode() == 0))
}

// Class names the failure.
func (e *Error) Class() string { return e.class }

// classify sorts a go-mail failure. A *mail.SendError is the server refusing the message; go-mail
// wraps a failed AUTH and a missing STARTTLS in plain errors, told apart by its own wording. AUTH
// runs inside the dial, so it is checked before a dial failure. go-mail never offers a password
// over an unencrypted connection, PLAIN and LOGIN included, so a password over plain SMTP to
// loopback fails here as auth.
func classify(err error) string {
	var se *mail.SendError
	var ne net.Error
	msg := err.Error()
	switch {
	case errors.As(err, &se):
		return "rejected"
	case strings.Contains(strings.ToLower(msg), "smtp auth"):
		return "auth"
	case strings.Contains(msg, "STARTTLS") || strings.Contains(msg, "tls:"):
		return "tls"
	case errors.As(err, &ne), strings.HasPrefix(msg, "dial failed"):
		return "unreachable"
	}
	return "rejected"
}
