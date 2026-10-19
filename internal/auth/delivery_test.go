package auth_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/invite"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// scriptedMailer stands in for an SMTP server whose send always goes out. entered, when set, is
// closed as a send begins, which then waits for release; after runs once the message is out.
type scriptedMailer struct {
	mu      sync.Mutex
	sent    int
	entered chan struct{}
	release chan struct{}
	after   func()
	outcome error
}

func (m *scriptedMailer) Send(ctx context.Context, _, _, _ string) error {
	if m.entered != nil {
		close(m.entered)
		<-m.release
	}
	m.mu.Lock()
	m.sent++
	m.mu.Unlock()
	if m.after != nil {
		m.after()
	}
	// The message is out; a send that honoured a cancelled context would still report it here.
	if m.outcome != nil {
		return m.outcome
	}
	return ctx.Err()
}

func (m *scriptedMailer) count() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.sent
}

// lostMarks is the store with every "sent" record failing, as a lost database connection would.
type lostMarks struct{ *pgstore.Store }

func (lostMarks) MarkInvitationEmailed(context.Context, string) error {
	return errors.New("connection lost")
}

// emailedOf calls createOrganisationInvitation and returns its status and emailed, which may be null.
func (w *inviteWeb) emailedOf(key string) (int, *bool) {
	w.f.t.Helper()
	status, body := w.f.send(w.h, call{
		method: http.MethodPost, path: instanceOrg("acme") + "/invitations", body: `{"email":"first@example.com","role":"Admin"}`,
		token: instanceToken, headers: map[string]string{"Idempotency-Key": key},
	})
	var c struct{ Emailed *bool }
	if err := json.Unmarshal(body, &c); err != nil {
		w.f.t.Fatalf("status %d: %v", status, err)
	}
	return status, c.Emailed
}

func show(b *bool) string {
	if b == nil {
		return "null"
	}
	return fmt.Sprint(*b)
}

// A sent email is never reported as unsent on a repeat, so a caller following the contract never
// sends the same invitation twice: not when the request is cancelled after the send, not when the
// record of the send is lost, and not when the repeat arrives during the send.
func TestRepeatNeverReportsASentEmailAsUnsent(t *testing.T) {
	cases := []struct {
		name string
		run  func(t *testing.T, w *inviteWeb, m *scriptedMailer) (first bool)
		// want is the repeat's emailed: true, or nil for unknown.
		want *bool
	}{
		{
			name: "request cancelled after the send",
			run: func(t *testing.T, w *inviteWeb, m *scriptedMailer) bool {
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				m.after = cancel
				c, err := w.links.Create(ctx, invite.Invite{
					OrganisationID: "acme", Email: "first@example.com", Role: "admin",
					BaseURL: "https://voicegateway.example", IdempotencyKey: "checkout-1",
				})
				if err != nil {
					t.Fatal(err)
				}
				return c.Emailed
			},
			want: new(true),
		},
		{
			name: "record of the send lost",
			run: func(t *testing.T, w *inviteWeb, _ *scriptedMailer) bool {
				w.links.Store = lostMarks{w.f.pg}
				status, emailed := w.emailedOf("checkout-1")
				w.links.Store = w.f.pg
				if status != http.StatusCreated {
					t.Fatalf("first call = %d", status)
				}
				// The first send's lease is long over when the repeat comes.
				w.links.Now = func() time.Time { return time.Now().Add(time.Hour) }
				return emailed != nil && *emailed
			},
			want: nil,
		},
		{
			name: "repeat during the send",
			run: func(t *testing.T, w *inviteWeb, m *scriptedMailer) bool {
				m.entered, m.release = make(chan struct{}), make(chan struct{})
				type answer struct {
					status  int
					emailed *bool
				}
				first, repeat := make(chan answer, 1), make(chan answer, 1)
				go func() {
					s, e := w.emailedOf("checkout-1")
					first <- answer{s, e}
				}()
				<-m.entered
				go func() {
					s, e := w.emailedOf("checkout-1")
					repeat <- answer{s, e}
				}()
				select {
				case a := <-repeat:
					t.Fatalf("the repeat answered %d %s before the send finished", a.status, show(a.emailed))
				case <-time.After(500 * time.Millisecond):
				}
				close(m.release)
				a, r := <-first, <-repeat
				if a.status != http.StatusCreated || r.status != http.StatusOK {
					t.Fatalf("first = %d, repeat = %d, want 201 and 200", a.status, r.status)
				}
				if r.emailed == nil || !*r.emailed {
					t.Fatalf("the repeat during the send = %s, want true", show(r.emailed))
				}
				return a.emailed != nil && *a.emailed
			},
			want: new(true),
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFixture(t)
			m := &scriptedMailer{}
			w := f.webWith(m, instanceToken)
			if status, _ := w.put("acme", "Acme"); status != http.StatusCreated {
				t.Fatalf("putOrganisation = %d", status)
			}
			if !tc.run(t, w, m) {
				t.Error("the first call reported its sent email as unsent")
			}
			status, emailed := w.emailedOf("checkout-1")
			if status != http.StatusOK || show(emailed) != show(tc.want) {
				t.Errorf("repeat = %d emailed %s, want 200 emailed %s", status, show(emailed), show(tc.want))
			}
			if n := m.count(); n != 1 {
				t.Errorf("%d emails sent, want 1", n)
			}
		})
	}
}

type uncertainDelivery struct{}

func (uncertainDelivery) Error() string         { return "final SMTP acknowledgement lost" }
func (uncertainDelivery) DeliveryUnknown() bool { return true }

func TestUnknownSMTPOutcomeIsNeverReportedUnsent(t *testing.T) {
	f := newFixture(t)
	m := &scriptedMailer{outcome: uncertainDelivery{}}
	w := f.webWith(m, instanceToken)
	if status, _ := w.put("acme", "Acme"); status != http.StatusCreated {
		t.Fatal(status)
	}
	if status, emailed := w.emailedOf("checkout-unknown"); status != http.StatusCreated || emailed != nil {
		t.Fatalf("first = %d %s, want 201 null", status, show(emailed))
	}
	w.links.Now = func() time.Time { return time.Now().Add(time.Hour) }
	if status, emailed := w.emailedOf("checkout-unknown"); status != http.StatusOK || emailed != nil {
		t.Fatalf("repeat = %d %s, want 200 null", status, show(emailed))
	}
	if m.count() != 1 {
		t.Fatal("uncertain delivery was resent")
	}
}
