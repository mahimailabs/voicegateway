package alert

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// rule is what a message says about one of the seven rules (alerts-model.ts). issue is the Issues
// page's check the rule watches.
type rule struct {
	title, severity, issue string
}

var rules = map[string]rule{
	"slow-answer":    {"Slow answers", "medium", "slow"},
	"cut-off":        {"Replies cut off", "medium", "cut"},
	"job-failed":     {"Agent job failed", "high", "job-failed"},
	"slow-join":      {"Slow agent join", "medium", "slow-join"},
	"plain-phone":    {"Unencrypted phone leg", "medium", "sip:plain-phone"},
	"recording-lost": {"Recording lost", "high", "recording-lost"},
	"ingress-error":  {"Ingress error", "high", "ingress-error"},
}

// agentRules group by the agent; the others by the component the occurrence is on.
func agentRule(id string) bool {
	switch id {
	case "slow-answer", "cut-off", "job-failed", "slow-join":
		return true
	}
	return false
}

// label names an incident's subject for people.
func label(ruleID, subject string) string {
	if agentRule(ruleID) {
		if subject == "" {
			return "Unknown agent"
		}
		return "Agent " + subject
	}
	return subject
}

// Message is a delivery's body: the webhook's JSON as sent, and what the email is written from. It
// is stored with the delivery, so a retry sends the same thing.
type Message struct {
	Type           string    `json:"type"`
	At             time.Time `json:"at"`
	OrganisationID string    `json:"organisationId"`
	ProjectID      string    `json:"projectId"`
	Incident       *Incident `json:"incident"`
	// URL opens the incident's history in the console; null without VOICEGATEWAY_PUBLIC_URL.
	URL *string `json:"url"`
}

// Incident is an incident as a message carries it.
type Incident struct {
	ID       string `json:"id"`
	RuleID   string `json:"ruleId"`
	Title    string `json:"title"`
	Severity string `json:"severity"`
	Subject  string `json:"subject"`
	State    string `json:"state"`
	// Occurrences counts every occurrence since the incident opened; SinceLastMessage those since
	// the previous message about it.
	Occurrences      int        `json:"occurrences"`
	SinceLastMessage int        `json:"sinceLastMessage"`
	OpenedAt         time.Time  `json:"openedAt"`
	ResolvedAt       *time.Time `json:"resolvedAt"`
	Last             Occurrence `json:"lastOccurrence"`
	HoldReason       *string    `json:"holdReason"`
	SilentSince      *time.Time `json:"silentSince"`
}

// Occurrence is the latest thing the incident's rule matched.
type Occurrence struct {
	At     *time.Time `json:"at"`
	Where  string     `json:"where"`
	Detail string     `json:"detail"`
	CallID *string    `json:"callId"`
	// CallURL opens the call in the console; null without a call or a public URL.
	CallURL *string `json:"callUrl"`
}

// Composer builds messages. PublicURL is VOICEGATEWAY_PUBLIC_URL, the base of console links.
type Composer struct {
	PublicURL string
	Now       func() time.Time
}

// consoleURL is a link to a page of a project's console, or nil without a public URL.
func (c Composer) consoleURL(organisationID, projectID, page string) *string {
	if c.PublicURL == "" {
		return nil
	}
	u := strings.TrimRight(c.PublicURL, "/") + "/org/" + url.PathEscape(organisationID) + "/project/" + url.PathEscape(projectID) + page
	return &u
}

// Compose is the store.AlertCompose for incident changes. Both channels carry the same message.
func (c Composer) Compose(inc store.AlertIncident, change, _ string) (json.RawMessage, error) {
	r, ok := rules[inc.RuleID]
	if !ok {
		return nil, fmt.Errorf("alert rule %q unknown", inc.RuleID)
	}
	m := Message{
		Type: "alert." + change, At: c.Now().UTC(), OrganisationID: inc.OrganisationID, ProjectID: inc.ProjectID,
		URL: c.consoleURL(inc.OrganisationID, inc.ProjectID, "/alerts?tab=history"),
		Incident: &Incident{
			ID: inc.ID, RuleID: inc.RuleID, Title: r.title, Severity: r.severity, Subject: inc.SubjectLabel, State: inc.State,
			Occurrences: inc.Occurrences, SinceLastMessage: inc.SinceLastMessage, OpenedAt: inc.OpenedAt, ResolvedAt: inc.ResolvedAt,
			Last:       Occurrence{At: inc.LastOccurrenceAt, Where: inc.Last.Where, Detail: inc.Last.Detail, CallID: inc.Last.CallID},
			HoldReason: inc.HoldReason, SilentSince: inc.SilentSince,
		},
	}
	if inc.Last.CallID != nil {
		m.Incident.Last.CallURL = c.consoleURL(inc.OrganisationID, inc.ProjectID, "/calls/"+url.PathEscape(*inc.Last.CallID))
	}
	return json.Marshal(m)
}

// Test is the message of a channel's "Send test".
func (c Composer) Test(organisationID, projectID string) (json.RawMessage, error) {
	return json.Marshal(Message{
		Type: "alert.test", At: c.Now().UTC(), OrganisationID: organisationID, ProjectID: projectID,
		URL: c.consoleURL(organisationID, projectID, "/alerts"),
	})
}

// email writes a message as an email's subject and plain-text body.
func email(m Message) (subject, body string) {
	var b strings.Builder
	if m.Incident == nil {
		b.WriteString("This is a test alert from VoiceGateway. The email channel of this project works.\n")
		if m.URL != nil {
			fmt.Fprintf(&b, "\nAlerts: %s\n", *m.URL)
		}
		return "[VoiceGateway] Test alert", b.String()
	}
	inc := m.Incident
	verb := map[string]string{"alert.firing": "Firing", "alert.reminder": "Still firing", "alert.resolved": "Resolved"}[m.Type]
	subject = fmt.Sprintf("[VoiceGateway] %s: %s, %s", verb, inc.Title, inc.Subject)
	fmt.Fprintf(&b, "%s: %s (%s severity)\n", verb, inc.Title, inc.Severity)
	fmt.Fprintf(&b, "Subject: %s\n", inc.Subject)
	fmt.Fprintf(&b, "Opened: %s\n", inc.OpenedAt.Format(time.RFC1123))
	if inc.ResolvedAt != nil {
		fmt.Fprintf(&b, "Resolved: %s\n", inc.ResolvedAt.Format(time.RFC1123))
	}
	fmt.Fprintf(&b, "Occurrences: %d in total", inc.Occurrences)
	if m.Type == "alert.reminder" {
		fmt.Fprintf(&b, ", %d since the last message", inc.SinceLastMessage)
	}
	b.WriteString("\n\nLatest:")
	if inc.Last.Where != "" {
		fmt.Fprintf(&b, " %s:", inc.Last.Where)
	}
	fmt.Fprintf(&b, " %s\n", inc.Last.Detail)
	if inc.Last.At != nil {
		fmt.Fprintf(&b, "At: %s\n", inc.Last.At.Format(time.RFC1123))
	}
	if inc.Last.CallURL != nil {
		fmt.Fprintf(&b, "Call: %s\n", *inc.Last.CallURL)
	}
	if inc.HoldReason != nil {
		fmt.Fprintf(&b, "\nStill firing because: %s\n", *inc.HoldReason)
	}
	if m.URL != nil {
		fmt.Fprintf(&b, "\nHistory: %s\n", *m.URL)
	}
	fmt.Fprintf(&b, "\nIncident %s, project %s.\n", inc.ID, m.ProjectID)
	return subject, b.String()
}
