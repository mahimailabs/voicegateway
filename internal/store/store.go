package store

import (
	"context"
	"errors"
	"time"
)

// Project is one LiveKit project inside an organisation (decision 0016). The LiveKit API secret is
// not part of it: only ingest opens it, through the store that sealed it.
type Project struct {
	OrganisationID string
	ID             string
	Name           string
	LiveKitURL     string
	LiveKitAPIKey  string
	CreatedAt      time.Time
}

// ErrLiveKitKeyInUse means another project already uses the LiveKit API key, which ingest relies on
// to find the project an event belongs to.
var ErrLiveKitKeyInUse = errors.New("LiveKit API key is already used by another project")

// ErrProjectExists means the organisation already has a project with that id.
var ErrProjectExists = errors.New("a project with this id already exists")

// ErrBadRow means storage refused a row for what it holds, not because it is unreachable or busy:
// retrying the same row cannot succeed.
var ErrBadRow = errors.New("row refused by storage")

// ErrNotFound means no record matched.
var ErrNotFound = errors.New("not found")

// ErrLastAdmin means a change would leave an organisation without an admin.
var ErrLastAdmin = errors.New("an organisation needs at least one admin")

// ErrMemberExists means the user already belongs to the organisation.
var ErrMemberExists = errors.New("the user is already a member of this organisation")

// Event is one ingested envelope as the worker stores it, with what joining it to a call found.
type Event struct {
	OrganisationID string
	ProjectID      string
	Kind           string
	// CallID is the call the event resolved to, empty when it resolved to none.
	CallID string
	// DedupeKey is the source's event id when it sent one, else the stream entry id.
	DedupeKey string
	// EventID is the source's own id, empty when unknown.
	EventID    string
	StreamID   string
	ReceivedAt time.Time
	Payload    []byte
	// Spans, TraceCalls, Unmatched and MetricPoints are stored with the event, before it, in its organisation
	// and project.
	Spans        []Span
	TraceCalls   []TraceCall
	Unmatched    []Unmatched
	MetricPoints []MetricPoint
}

// EventKey is what makes an Event unique.
type EventKey struct {
	OrganisationID string
	ProjectID      string
	Kind           string
	DedupeKey      string
}

// Key returns the event's dedupe key.
func (e Event) Key() EventKey {
	return EventKey{OrganisationID: e.OrganisationID, ProjectID: e.ProjectID, Kind: e.Kind, DedupeKey: e.DedupeKey}
}

// Span is one OTLP span decoded from a trace export.
type Span struct {
	TraceID      string
	SpanID       string
	ParentSpanID string
	// CallID is the call the span joined by its own ids, empty when it carries none.
	CallID     string
	Name       string
	Kind       string
	Start, End time.Time
	StatusCode string
	// RoomID and JobID are the span's own room_id and job_id attributes as LiveKit Agents stamps
	// them, empty when absent. Resource attributes are never read for them.
	RoomID, JobID string
	ScopeName     string
	Attributes    map[string]string
	Resource      map[string]string
}

// TraceCall links a trace to the call one of its spans joined.
type TraceCall struct {
	TraceID string
	CallID  string
}

// Unmatched records an event or trace that resolved to no call, so it is counted once.
type Unmatched struct {
	Kind   string
	Key    string
	Reason string
}

// CallUpsert is what one source reported about a room instance. A nil time was not reported.
type CallUpsert struct {
	OrganisationID string
	ProjectID      string
	RoomSID        string
	RoomName       string
	JobID          string
	StartedAt      *time.Time
	LastActivityAt *time.Time
	// FirstSeenAt is the earliest time this source reported for the call.
	FirstSeenAt *time.Time
}

// Participant is one participant of a call. Kind is LiveKit's ParticipantInfo.Kind, lower case.
type Participant struct {
	CallID           string
	SID              string
	Identity         string
	Kind             string
	JoinedAt, LeftAt *time.Time
}

// CallRef is a stored call and its end, when one was set, so a joiner can tell whether a reported
// time falls after the call ended.
type CallRef struct {
	ID      string
	EndedAt *time.Time
}

// MetricPoint is one data point of an agent's lk.agents.usage.* counter, from an OTLP metric
// export. LiveKit Agents exports these with delta temporality, so a call's points add up.
type MetricPoint struct {
	Name string
	// CallID is the call the point joined by its own room_id or job_id, empty when it joined none.
	CallID     string
	Value      float64
	Start, End time.Time
	// RoomID and JobID are the point's own attributes; the resource's are never read.
	RoomID, JobID string
	Attributes    map[string]string
}

// Call is a stored call as the API reads it. A nil time or empty string was not reported.
type Call struct {
	ID       string
	RoomName string
	RoomSID  string
	JobID    string
	// StartedAt is the room start a webhook or session report gave; FirstSeenAt is the earliest
	// time any joined source reported.
	StartedAt, EndedAt, FirstSeenAt *time.Time
	EndState                        string
	// Participants is how many participants webhooks reported for the call.
	Participants int64
	// Servable is the API's one rule for which calls it serves: room and start known after the
	// fallbacks (the call_list view).
	Servable bool
	// Summarized is true once the summary sweep has built the call's summary, and with it healed
	// any room or start missing from a call stored before phase 04d.
	Summarized bool
}

// ListSnapshot is a listCalls snapshot kept server-side: the scope and filters it was taken for,
// when, and the first page's totals as the API encoded them.
type ListSnapshot struct {
	ID, OrganisationID, ProjectID, Filters string
	At                                     time.Time
	Totals                                 []byte
}

// StaleCall is a call whose summary needs rebuilding, with the mark the rebuild read.
type StaleCall struct {
	OrganisationID, ProjectID, ID string
	StaleAt                       time.Time
}

// CallSummary is the per-call figures listCalls reads. A nil value is unknown.
type CallSummary struct {
	TurnCount      *int
	AnswerMs       []float64
	AnswerMsMedian *float64
	Models         []string
	Pipeline       string
	AgentKey       *string
	// RoomName and FirstSeenAt are what the spans report, to fill a call stored before 04d.
	RoomName    string
	FirstSeenAt *time.Time
}

// CallFilter is a listCalls request as storage reads it. A nil bound is no bound.
type CallFilter struct {
	OrganisationID, ProjectID string
	// SnapshotAt leaves out calls written after it.
	SnapshotAt                                     time.Time
	From, To                                       *time.Time
	Room                                           string
	Channels                                       []string
	AnswerMin, AnswerMax, DurationMin, DurationMax *float64
	TurnsMin, TurnsMax                             *int
	UnknownAnswer, UnknownDuration, UnknownTurns   bool
	// MatchNone is set when a filter no stored value can satisfy (issues, tags, archived=true,
	// recorded=true), so the list is empty rather than the filter ignored.
	MatchNone bool
	// SortBy is startedAt, durationSeconds, turnCount, answerMs or issues; issues has no stored
	// value, so it orders by id.
	SortBy     string
	Descending bool
	// BudgetMs is the answer budget totals count against.
	BudgetMs float64
}

// CallCursor is where a page ends: the last row's sort key, whether it was unknown, and its id.
type CallCursor struct {
	Unknown bool
	Value   float64
	ID      string
}

// CallListRow is one listCalls row. A nil value is unknown.
type CallListRow struct {
	ID, RoomName, RoomSID string
	// StartAt is the started-at the API shows: the reported room start, else the first seen time.
	StartAt            time.Time
	StartedAt, EndedAt *time.Time
	EndState           string
	DurationSeconds    *float64
	Participants       int64
	Channel            string
	TurnCount          *int
	AnswerMs           []float64
	AnswerMsMedian     *float64
	Models             []string
	Pipeline           string
	AgentKey           *string
	Cursor             CallCursor
}

// CallTotals is listCalls' totals over every matching call as of the snapshot.
type CallTotals struct {
	CallCount, Phone, Web, Unknown int64
	AnswerTurns, OverBudget        int64
	P50Ms                          *float64
	ValuesMs                       []float64
	// ValuesTruncated is true when more answers matched than ValuesMs holds.
	ValuesTruncated bool
}

// Principal is who a request is from: a signed-in user with their organisation roles, or, until
// phase 06c, the interim API token, which names no user.
type Principal struct {
	UserID string
	Email  string
	// Roles maps each organisation the user belongs to to their role there.
	Roles map[string]string
	// Token is true for the interim bearer token.
	Token bool
	// PasswordVersion is the user's current password_version; a session holding another is signed
	// out.
	PasswordVersion int
}

type principalKey struct{}

// WithPrincipal returns ctx carrying p.
func WithPrincipal(ctx context.Context, p Principal) context.Context {
	return context.WithValue(ctx, principalKey{}, p)
}

// PrincipalFrom returns the principal a gate put on ctx, and whether there is one.
func PrincipalFrom(ctx context.Context) (Principal, bool) {
	p, ok := ctx.Value(principalKey{}).(Principal)
	return p, ok
}

// User is an account as sign-in reads it.
type User struct {
	ID, Email       string
	PasswordHash    string
	PasswordVersion int
}

// Member is one user's membership of an organisation; Role is the stored admin, member or viewer.
type Member struct {
	UserID, Email, Role string
}

// Workspace is an organisation a user belongs to, with its projects.
type Workspace struct {
	ID, Name string
	Projects []WorkspaceProject
}

// WorkspaceProject is one project of a Workspace.
type WorkspaceProject struct{ ID, Name string }

// SignInKey is one key sign-in attempts are counted under: an email or a client address, with how
// many attempts it may have in the window.
type SignInKey struct {
	Kind, Key string
	Max       int
}
