package api

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// listCalls limits (docs/api/openapi.yaml, operation listCalls).
const (
	defaultLimit = 25
	maxLimit     = 100
	maxValues    = 1000
)

// callPage is the contract's CallPage.
type callPage struct {
	Items  []callSummary `json:"items"`
	Page   page          `json:"page"`
	Totals callTotals    `json:"totals"`
}

type page struct {
	NextCursor *string `json:"nextCursor"`
	Snapshot   string  `json:"snapshot"`
}

type callSummary struct {
	ID               string     `json:"id"`
	Room             string     `json:"room"`
	RoomSID          *string    `json:"roomSid"`
	StartedAt        time.Time  `json:"startedAt"`
	EndedAt          *time.Time `json:"endedAt"`
	DurationSeconds  *float64   `json:"durationSeconds"`
	ParticipantCount *int64     `json:"participantCount"`
	EndState         string     `json:"endState"`
	Channel          string     `json:"channel"`
	Pipeline         string     `json:"pipeline"`
	Models           []string   `json:"models"`
	AgentKey         *string    `json:"agentKey"`
	TurnCount        *int       `json:"turnCount"`
	AnswerMs         []float64  `json:"answerMs"`
	AnswerMsMedian   *float64   `json:"answerMsMedian"`
	Issues           issueCount `json:"issues"`
	Tags             []string   `json:"tags"`
	Archived         bool       `json:"archived"`
	Recorded         bool       `json:"recorded"`
}

type issueCount struct {
	High   *int64   `json:"high"`
	Medium *int64   `json:"medium"`
	Titles []string `json:"titles"`
}

type callTotals struct {
	CallCount *int64       `json:"callCount"`
	ByChannel channelCount `json:"byChannel"`
	Answers   answerTotals `json:"answers"`
	Issues    issueTotals  `json:"issues"`
	Recorded  *int64       `json:"recorded"`
}

type channelCount struct {
	Phone   *int64 `json:"phone"`
	Web     *int64 `json:"web"`
	Console *int64 `json:"console"`
	Local   *int64 `json:"local"`
	Unknown *int64 `json:"unknown"`
}

type answerTotals struct {
	Turns           *int64    `json:"turns"`
	OverBudget      *int64    `json:"overBudget"`
	BudgetMs        float64   `json:"budgetMs"`
	P50Ms           *float64  `json:"p50Ms"`
	ValuesMs        []float64 `json:"valuesMs"`
	ValuesTruncated bool      `json:"valuesTruncated"`
}

type issueTotals struct {
	High    *int64 `json:"high"`
	Medium  *int64 `json:"medium"`
	None    *int64 `json:"none"`
	Unknown *int64 `json:"unknown"`
}

// cursor is what the opaque cursor carries: where the page ended, and a fingerprint of the scope,
// filters, sort, order and snapshot it was issued for.
type cursor struct {
	Bound   string  `json:"b"`
	Unknown bool    `json:"u"`
	Value   float64 `json:"v"`
	ID      string  `json:"id"`
}

var (
	sorts    = []string{"startedAt", "durationSeconds", "turnCount", "answerMs", "issues"}
	channels = []string{"phone", "web", "console", "local", "unknown"}
	issues   = []string{"high", "medium", "none"}
	unknowns = []string{"answerMs", "durationSeconds", "turnCount"}
)

// errBadRequest marks a request that does not meet the operation's parameters.
var errBadRequest = errors.New("invalid request")

// listCalls serves operationId listCalls.
func (a *API) listCalls(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	org, project := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(ctx, org, project); err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	q := r.URL.Query()
	f, limit, err := a.parseList(org, project, q)
	if err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
		return
	}

	settings, ok := a.readSettings(w, r)
	if !ok {
		return
	}
	f.BudgetMs = settings.Thresholds.AnswerMs

	// The snapshot is kept server-side under a short id, so the token the console carries in its
	// URL stays small; it is bound to the scope and filters it was taken for.
	filters := fingerprint(org, project, q, "", settings.Revision)
	var (
		snap   store.ListSnapshot
		totals callTotals
		after  *store.CallCursor
	)
	token, cursorToken := q.Get("snapshot"), q.Get("cursor")
	switch {
	case cursorToken != "" && token == "":
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "cursor needs the snapshot it was issued with")
		return
	case token != "" && !snapshotID.MatchString(token):
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "snapshot is not one this server issued")
		return
	case token != "":
		snap, err = a.Calls.ListSnapshot(ctx, token)
		if err != nil && !errors.Is(err, store.ErrNotFound) {
			a.fail(w, r, err, "")
			return
		}
		if err != nil || snap.OrganisationID != org || snap.ProjectID != project || snap.Filters != filters || json.Unmarshal(snap.Totals, &totals) != nil {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "snapshot was taken for another project or other filters, or has expired")
			return
		}
		f.SnapshotAt = snap.At
	}
	if cursorToken != "" {
		var c cursor
		if err := decode(cursorToken, &c); err != nil || c.Bound != fingerprint(org, project, q, token, settings.Revision) {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "cursor was issued for other filters, sort, order or snapshot")
			return
		}
		after = &store.CallCursor{Unknown: c.Unknown, Value: c.Value, ID: c.ID}
	}

	first := token == ""
	rows, pageTotals, at, err := a.Calls.ListCalls(ctx, f, after, limit+1, first, maxValues)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	if first {
		totals = toTotals(pageTotals, f.BudgetMs)
		body, err := json.Marshal(totals)
		if err == nil {
			token, err = randomID()
		}
		if err == nil {
			err = a.Calls.PutListSnapshot(ctx, store.ListSnapshot{ID: token, OrganisationID: org, ProjectID: project, Filters: filters, At: at, Totals: body})
		}
		if err != nil {
			a.fail(w, r, err, "")
			return
		}
	}
	bound := fingerprint(org, project, q, token, settings.Revision)

	out := callPage{Items: make([]callSummary, 0, min(len(rows), limit)), Page: page{Snapshot: token}, Totals: totals}
	for i, row := range rows {
		if i == limit {
			next, err := encode(cursor{Bound: bound, Unknown: rows[i-1].Cursor.Unknown, Value: rows[i-1].Cursor.Value, ID: rows[i-1].Cursor.ID})
			if err != nil {
				a.fail(w, r, err, "")
				return
			}
			out.Page.NextCursor = &next
			break
		}
		out.Items = append(out.Items, toSummary(row))
	}
	writeJSON(w, http.StatusOK, out)
}

// parseList reads the operation's query parameters into a filter. Any value outside its schema
// is errBadRequest.
func (a *API) parseList(org, project string, q url.Values) (store.CallFilter, int, error) {
	f := store.CallFilter{OrganisationID: org, ProjectID: project, SortBy: "startedAt", Descending: true}
	bad := func(format string, args ...any) (store.CallFilter, int, error) {
		return store.CallFilter{}, 0, fmt.Errorf("%w: "+format, append([]any{errBadRequest}, args...)...)
	}

	limit := defaultLimit
	if v := q.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > maxLimit {
			return bad("limit must be an integer from 1 to %d", maxLimit)
		}
		limit = n
	}
	for _, name := range []string{"from", "to"} {
		v := q.Get(name)
		if v == "" {
			continue
		}
		t, err := time.Parse(time.RFC3339Nano, v)
		if err != nil {
			return bad("%s must be an RFC 3339 date-time", name)
		}
		if name == "from" {
			f.From = &t
		} else {
			f.To = &t
		}
	}
	f.Room = q.Get("room")
	var err error
	if f.Channels, err = enums(q, "channel", channels); err != nil {
		return bad("%v", err)
	}
	issueFilter, err := enums(q, "issues", issues)
	if err != nil {
		return bad("%v", err)
	}
	unknown, err := enums(q, "unknown", unknowns)
	if err != nil {
		return bad("%v", err)
	}
	f.UnknownAnswer, f.UnknownDuration, f.UnknownTurns = slices.Contains(unknown, "answerMs"), slices.Contains(unknown, "durationSeconds"), slices.Contains(unknown, "turnCount")
	for _, b := range []struct {
		name string
		dst  **float64
	}{{"answerMsMin", &f.AnswerMin}, {"answerMsMax", &f.AnswerMax}, {"durationSecondsMin", &f.DurationMin}, {"durationSecondsMax", &f.DurationMax}} {
		if v := q.Get(b.name); v != "" {
			n, err := strconv.ParseFloat(v, 64)
			if err != nil || n < 0 || math.IsNaN(n) || math.IsInf(n, 0) {
				return bad("%s must be a finite number of at least 0", b.name)
			}
			*b.dst = &n
		}
	}
	for _, b := range []struct {
		name string
		dst  **int
	}{{"turnsMin", &f.TurnsMin}, {"turnsMax", &f.TurnsMax}} {
		if v := q.Get(b.name); v != "" {
			n, err := strconv.Atoi(v)
			if err != nil || n < 0 {
				return bad("%s must be an integer of at least 0", b.name)
			}
			*b.dst = &n
		}
	}
	tags := q["tag"]
	for _, t := range tags {
		if t == "" {
			return bad("tag must not be empty")
		}
	}
	archived, err := optionalBool(q, "archived")
	if err != nil {
		return bad("%v", err)
	}
	recorded, err := optionalBool(q, "recorded")
	if err != nil {
		return bad("%v", err)
	}
	// No stored value can satisfy these yet: every call's issues are unknown, and the server holds
	// no tags, archive or recordings. archived=false and recorded=false match every call.
	f.MatchNone = len(issueFilter) > 0 || len(tags) > 0 || (archived != nil && *archived) || (recorded != nil && *recorded)

	if v := q.Get("sort"); v != "" {
		if !slices.Contains(sorts, v) {
			return bad("sort must be one of %s", strings.Join(sorts, ", "))
		}
		f.SortBy = v
	}
	switch q.Get("order") {
	case "", "desc":
	case "asc":
		f.Descending = false
	default:
		return bad("order must be asc or desc")
	}
	return f, limit, nil
}

func enums(q url.Values, name string, allowed []string) ([]string, error) {
	vs := q[name]
	for _, v := range vs {
		if !slices.Contains(allowed, v) {
			return nil, fmt.Errorf("%s must be one of %s", name, strings.Join(allowed, ", "))
		}
	}
	return vs, nil
}

func optionalBool(q url.Values, name string) (*bool, error) {
	v := q.Get(name)
	if v == "" {
		return nil, nil
	}
	b, err := strconv.ParseBool(v)
	if err != nil || (v != "true" && v != "false") {
		return nil, fmt.Errorf("%s must be true or false", name)
	}
	return &b, nil
}

// fingerprint binds a snapshot to the scope and filters it was taken for, and a cursor to those
// plus its snapshot id: every query parameter except the paging ones, in a canonical order.
func fingerprint(org, project string, q url.Values, snapshotID string, settingsRevision int64) string {
	keys := make([]string, 0, len(q))
	for k := range q {
		if k != "cursor" && k != "snapshot" && k != "limit" {
			keys = append(keys, k)
		}
	}
	slices.Sort(keys)
	h := sha256.New()
	_, _ = fmt.Fprintf(h, "org=%s\nproject=%s\n", org, project) // a hash never fails to write
	for _, k := range keys {
		vs := slices.Clone(q[k])
		slices.Sort(vs)
		_, _ = fmt.Fprintf(h, "%s=%s\n", k, strings.Join(vs, "\x00"))
	}
	_, _ = fmt.Fprintf(h, "snapshot=%s\nsettingsRevision=%d\n", snapshotID, settingsRevision)
	return hex.EncodeToString(h.Sum(nil))[:24]
}

// snapshotID is what randomID makes: 16 bytes, base64url without padding.
var snapshotID = regexp.MustCompile(`^[A-Za-z0-9_-]{22}$`)

// randomID is a snapshot id: 16 random bytes, URL-safe.
func randomID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", fmt.Errorf("snapshot id: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(b[:]), nil
}

func encode(v any) (string, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return "", fmt.Errorf("encode page token: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func decode(token string, v any) error {
	b, err := base64.RawURLEncoding.DecodeString(token)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

func toSummary(r store.CallListRow) callSummary {
	s := callSummary{
		ID: r.ID, Room: r.RoomName, RoomSID: text(r.RoomSID), StartedAt: r.StartAt, EndedAt: r.EndedAt,
		DurationSeconds: r.DurationSeconds, EndState: r.EndState, Channel: r.Channel, Pipeline: r.Pipeline,
		Models: r.Models, AgentKey: r.AgentKey, TurnCount: r.TurnCount, AnswerMs: r.AnswerMs, AnswerMsMedian: r.AnswerMsMedian,
		Issues: issueCount{Titles: []string{}}, Tags: []string{},
	}
	if s.Models == nil {
		s.Models = []string{}
	}
	if r.Participants > 0 {
		n := r.Participants
		s.ParticipantCount = &n
	}
	return s
}

func toTotals(t *store.CallTotals, budget float64) callTotals {
	n := func(v int64) *int64 { return &v }
	out := callTotals{
		CallCount: n(t.CallCount),
		// Console and local are null, not 0: no stored source tells those channels apart yet, the
		// same rule getCallStats follows.
		ByChannel: channelCount{Phone: n(t.Phone), Web: n(t.Web), Unknown: n(t.Unknown)},
		Answers: answerTotals{
			Turns: n(t.AnswerTurns), OverBudget: n(t.OverBudget), BudgetMs: budget, P50Ms: t.P50Ms,
			ValuesMs: t.ValuesMs, ValuesTruncated: t.ValuesTruncated,
		},
	}
	if out.Answers.ValuesMs == nil {
		out.Answers.ValuesMs = []float64{}
	}
	return out
}
