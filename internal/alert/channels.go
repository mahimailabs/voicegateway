package alert

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/PagerDuty/go-pagerduty"
	"github.com/atc0005/go-teams-notify/v2/adaptivecard"
	"github.com/bwmarrin/discordgo"
	ogalert "github.com/opsgenie/opsgenie-go-sdk-v2/alert"
	ogclient "github.com/opsgenie/opsgenie-go-sdk-v2/client"
	"github.com/sirupsen/logrus"
	"github.com/slack-go/slack"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Provider endpoints by region (docs/specs/alert-channels.md). Tests point them at a local server.
var (
	pagerDutyEndpoints = map[string]string{"us": "https://events.pagerduty.com", "eu": "https://events.eu.pagerduty.com"}
	opsgenieHosts      = map[string]ogclient.ApiUrl{"us": ogclient.API_URL, "eu": ogclient.API_URL_EU}
)

// summary is a message as the chat and paging channels write it: one title line, the facts, and
// links into the console.
type summary struct {
	// Title is "Firing: Slow answers, Agent support" or "VoiceGateway test alert".
	Title    string
	Severity string
	Facts    [][2]string
	// Text is the latest occurrence, or what a test proves.
	Text    string
	Link    *string
	CallURL *string
	Test    bool
}

var changeVerbs = map[string]string{"alert.firing": "Firing", "alert.reminder": "Still firing", "alert.resolved": "Resolved"}

func summarize(m Message, kind string) summary {
	if m.Incident == nil {
		return summary{
			Title: "VoiceGateway test alert", Test: true, Link: m.URL,
			Text: "The " + channelNames[kind] + " channel of this project works.",
		}
	}
	inc := m.Incident
	s := summary{
		Title:    fmt.Sprintf("%s: %s, %s", changeVerbs[m.Type], inc.Title, inc.Subject),
		Severity: inc.Severity, Link: m.URL, CallURL: inc.Last.CallURL,
		Facts: [][2]string{{"Severity", inc.Severity}, {"Opened", inc.OpenedAt.UTC().Format(time.RFC1123)}},
	}
	if inc.ResolvedAt != nil {
		s.Facts = append(s.Facts, [2]string{"Resolved", inc.ResolvedAt.UTC().Format(time.RFC1123)})
	}
	occ := strconv.Itoa(inc.Occurrences) + " in total"
	if m.Type == "alert.reminder" {
		occ += ", " + strconv.Itoa(inc.SinceLastMessage) + " since the last message"
	}
	s.Facts = append(s.Facts, [2]string{"Occurrences", occ})
	if inc.HoldReason != nil {
		s.Facts = append(s.Facts, [2]string{"Still firing because", *inc.HoldReason})
	}
	switch {
	case inc.Last.Where != "" && inc.Last.Detail != "":
		s.Text = inc.Last.Where + ": " + inc.Last.Detail
	case inc.Last.Where != "":
		s.Text = inc.Last.Where
	case inc.Last.Detail != "":
		s.Text = inc.Last.Detail
	default:
		// Slack refuses a section with empty text (invalid_blocks), so the text is never empty.
		s.Text = inc.Title
	}
	return s
}

// chatMessage writes a message as Slack Block Kit, a Teams Adaptive Card or a Discord embed. Every
// text that comes from a call (agent names, details) goes as plain text, never markup, so it cannot
// mention a channel or a person.
func chatMessage(kind string, m Message) ([]byte, error) {
	s := summarize(m, kind)
	switch kind {
	case "slack":
		return json.Marshal(slackMessage(s))
	case "discord":
		return json.Marshal(discordMessage(s, m.At))
	case "teams":
		return teamsMessage(s)
	}
	return nil, fmt.Errorf("alert channel %q writes no chat message", kind)
}

func slackMessage(s summary) slack.WebhookMessage {
	plain := func(t string) *slack.TextBlockObject {
		return slack.NewTextBlockObject(slack.PlainTextType, t, false, false)
	}
	blocks := []slack.Block{slack.NewHeaderBlock(plain(clip(s.Title, 150)))}
	var fields []*slack.TextBlockObject
	for _, f := range s.Facts {
		fields = append(fields, plain(f[0]+": "+f[1]))
	}
	blocks = append(blocks, slack.NewSectionBlock(plain(clip(s.Text, 3000)), fields, nil))
	if buttons := slackButtons(s, plain); len(buttons) > 0 {
		blocks = append(blocks, slack.NewActionBlock("", buttons...))
	}
	// text is the notification's fallback; Slack reads <, > and & in it as markup.
	return slack.WebhookMessage{Text: slackEscape(s.Title), Blocks: &slack.Blocks{BlockSet: blocks}}
}

func slackButtons(s summary, plain func(string) *slack.TextBlockObject) []slack.BlockElement {
	var out []slack.BlockElement
	if s.CallURL != nil {
		out = append(out, slack.NewButtonBlockElement("call", "", plain("Open the call")).WithURL(*s.CallURL))
	}
	if s.Link != nil {
		out = append(out, slack.NewButtonBlockElement("history", "", plain("Alert history")).WithURL(*s.Link))
	}
	return out
}

var slackEscape = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace

// Discord embed colours: red for high, amber for medium, green once resolved, blue for a test.
var discordColours = map[string]int{"high": 0xD70015, "medium": 0xC93400, "resolved": 0x248A3D, "test": 0x0071E3}

func discordMessage(s summary, at time.Time) discordgo.WebhookParams {
	colour := discordColours[s.Severity]
	if s.Test {
		colour = discordColours["test"]
	} else if strings.HasPrefix(s.Title, "Resolved") {
		colour = discordColours["resolved"]
	}
	embed := &discordgo.MessageEmbed{
		Title: clip(s.Title, 256), Description: clip(s.Text, 4096), Color: colour,
		Timestamp: at.UTC().Format(time.RFC3339), Footer: &discordgo.MessageEmbedFooter{Text: "VoiceGateway"},
	}
	if s.Link != nil {
		embed.URL = *s.Link
	}
	for _, f := range s.Facts {
		embed.Fields = append(embed.Fields, &discordgo.MessageEmbedField{Name: f[0], Value: clip(f[1], 1024), Inline: true})
	}
	if s.CallURL != nil {
		embed.Fields = append(embed.Fields, &discordgo.MessageEmbedField{Name: "Call", Value: *s.CallURL})
	}
	return discordgo.WebhookParams{
		Username: "VoiceGateway", Embeds: []*discordgo.MessageEmbed{embed},
		// An empty parse list: nothing in the message pings anyone.
		AllowedMentions: &discordgo.MessageAllowedMentions{Parse: []discordgo.AllowedMentionType{}},
		Components:      []discordgo.MessageComponent{},
	}
}

func teamsMessage(s summary) ([]byte, error) {
	card := adaptivecard.NewCard()
	if err := card.AddElement(false, adaptivecard.NewTitleTextBlock(s.Title, true), adaptivecard.NewTextBlock(s.Text, true)); err != nil {
		return nil, err
	}
	if len(s.Facts) > 0 {
		facts := adaptivecard.NewFactSet()
		for _, f := range s.Facts {
			if err := facts.AddFact(adaptivecard.Fact{Title: f[0], Value: f[1]}); err != nil {
				return nil, err
			}
		}
		if err := card.AddFactSet(false, facts); err != nil {
			return nil, err
		}
	}
	for _, link := range []struct {
		url   *string
		title string
	}{{s.CallURL, "Open the call"}, {s.Link, "Alert history"}} {
		if link.url == nil {
			continue
		}
		action, err := adaptivecard.NewActionOpenURL(*link.url, link.title)
		if err != nil {
			return nil, err
		}
		if err := card.AddAction(false, action); err != nil {
			return nil, err
		}
	}
	msg, err := adaptivecard.NewMessageFromCard(card)
	if err != nil {
		return nil, err
	}
	if err := msg.Prepare(); err != nil {
		return nil, err
	}
	return io.ReadAll(msg.Payload())
}

// pagerDuty sends an incident change through the Events API v2: firing and reminders trigger, with
// the incident as the dedup key, so PagerDuty keeps one incident; resolved resolves it. A test is a
// change event, which checks the routing key and pages nobody.
func (d *Deliverer) pagerDuty(ctx context.Context, c store.AlertKeyConfig, m Message) outcome {
	endpoint, ok := pagerDutyEndpoints[c.Region]
	if !ok || c.Key == "" {
		return refused("bad_config", "the channel configuration could not be read")
	}
	client := pagerduty.NewClient("", pagerduty.WithV2EventsAPIEndpoint(endpoint))
	client.HTTPClient = d.HTTP
	s := summarize(m, "pagerduty")
	var err error
	if m.Incident == nil {
		ev := pagerduty.ChangeEvent{RoutingKey: c.Key, Payload: pagerduty.ChangeEventPayload{
			Summary: s.Title + ": " + s.Text, Source: "VoiceGateway", Timestamp: m.At.UTC().Format(time.RFC3339),
		}}
		if s.Link != nil {
			ev.Links = []pagerduty.ChangeEventLink{{Href: *s.Link, Text: "Alerts"}}
		}
		_, err = client.CreateChangeEventWithContext(ctx, ev)
	} else {
		_, err = client.ManageEventWithContext(ctx, pagerDutyEvent(c.Key, m, s))
	}
	return libraryOutcome(ctx, err, c.Key)
}

var pagerDutySeverity = map[string]string{"high": "critical", "medium": "warning"}

func pagerDutyEvent(key string, m Message, s summary) *pagerduty.V2Event {
	inc := m.Incident
	ev := &pagerduty.V2Event{RoutingKey: key, Action: "trigger", DedupKey: incidentKey(inc.ID), Client: "VoiceGateway"}
	if m.Type == "alert.resolved" {
		ev.Action = "resolve"
		return ev
	}
	details := map[string]string{"latest": s.Text, "project": m.ProjectID, "incident": inc.ID}
	for _, f := range s.Facts {
		details[strings.ToLower(f[0])] = f[1]
	}
	ev.Payload = &pagerduty.V2Payload{
		Summary: clip(s.Title, 1024), Source: inc.Subject, Severity: pagerDutySeverity[inc.Severity],
		Timestamp: m.At.UTC().Format(time.RFC3339), Component: inc.RuleID, Group: m.ProjectID, Class: inc.Title, Details: details,
	}
	if s.Link != nil {
		ev.ClientURL = *s.Link
	}
	for _, link := range []*string{s.CallURL, s.Link} {
		if link != nil {
			ev.Links = append(ev.Links, map[string]string{"href": *link})
		}
	}
	return ev
}

// Opsgenie priorities by rule severity.
var opsgeniePriority = map[string]ogalert.Priority{"high": ogalert.P2, "medium": ogalert.P3}

// opsgenie sends an incident change through the Alert API: firing and reminders create the alert
// under the incident's alias, which Opsgenie de-duplicates; resolved closes it. A test creates a P5
// alert.
func (d *Deliverer) opsgenie(ctx context.Context, c store.AlertKeyConfig, m Message) outcome {
	host, ok := opsgenieHosts[c.Region]
	if !ok || c.Key == "" {
		return refused("bad_config", "the channel configuration could not be read")
	}
	// The SDK drops the request's context (client.Exec discards req.WithContext), so the client's own
	// timeout bounds the attempt; it never retries, since the delivery's schedule does.
	hc := *d.HTTP
	hc.Timeout = sendTimeout
	quiet := logrus.New()
	quiet.SetOutput(io.Discard)
	client, err := ogalert.NewClient(&ogclient.Config{
		ApiKey: c.Key, OpsGenieAPIURL: host, HttpClient: &hc, Logger: quiet,
		RetryPolicy: func(context.Context, *http.Response, error) (bool, error) { return false, nil },
	})
	if err != nil {
		return refused("bad_config", "the channel configuration could not be read")
	}
	s := summarize(m, "opsgenie")
	switch {
	case m.Incident == nil:
		_, err = client.Create(ctx, &ogalert.CreateAlertRequest{
			Message: s.Title, Description: s.Text + " You can close this alert.", Alias: "voicegateway-test-" + m.ProjectID,
			Source: "VoiceGateway", Priority: ogalert.P5,
		})
	case m.Type == "alert.resolved":
		_, err = client.Close(ctx, &ogalert.CloseAlertRequest{
			IdentifierType: ogalert.ALIAS, IdentifierValue: incidentKey(m.Incident.ID), Source: "VoiceGateway", Note: s.Title,
		})
	default:
		details := map[string]string{"project": m.ProjectID, "incident": m.Incident.ID, "rule": m.Incident.RuleID}
		for _, f := range s.Facts {
			details[f[0]] = f[1]
		}
		for name, link := range map[string]*string{"call": s.CallURL, "history": s.Link} {
			if link != nil {
				details[name] = *link
			}
		}
		_, err = client.Create(ctx, &ogalert.CreateAlertRequest{
			Message: clip(s.Title, 130), Alias: incidentKey(m.Incident.ID), Description: clip(s.Text, 15000),
			Entity: m.Incident.Subject, Source: "VoiceGateway", Priority: opsgeniePriority[m.Incident.Severity],
			Tags: []string{"voicegateway", m.Incident.RuleID}, Details: details,
		})
	}
	return libraryOutcome(ctx, err, c.Key)
}

// libraryOutcome classifies an SDK's error the way post classifies an answer. key is taken out of
// the provider's message.
func libraryOutcome(ctx context.Context, err error, key string) outcome {
	if err == nil {
		return outcome{status: "HTTP 202", ok: true}
	}
	var pd pagerduty.APIError
	if errors.As(err, &pd) {
		msg := ""
		if pd.APIError.Valid {
			msg = strings.Join(append([]string{pd.APIError.ErrorObject.Message}, pd.APIError.ErrorObject.Errors...), " ")
		}
		return answered(pd.StatusCode, providerMessage([]byte(msg), "", key))
	}
	var og *ogclient.ApiError
	if errors.As(err, &og) {
		return answered(og.StatusCode, providerMessage([]byte(og.Message), "", key))
	}
	// The SDKs wrap network errors as text: a timeout shows as the context, or the client's own.
	if ctx.Err() != nil || strings.Contains(err.Error(), "Client.Timeout") {
		return timedOut()
	}
	return outcome{status: "unreachable", err: ptr("could not connect"), retry: true}
}

// incidentKey names an incident to a paging provider: PagerDuty's dedup key, Opsgenie's alias. The
// Opsgenie SDK puts the alias in the URL path unescaped, so it holds no slash.
func incidentKey(id string) string { return "voicegateway-" + id }

// clip keeps s within n bytes, on a rune boundary.
func clip(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return strings.ToValidUTF8(s[:n-3], "") + "..."
}
