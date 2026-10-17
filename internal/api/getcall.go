package api

import (
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

// call is the contract's Call. It holds no field the contract does not name
// (additionalProperties: false).
type call struct {
	ID               string      `json:"id"`
	Room             string      `json:"room"`
	RoomSID          *string     `json:"roomSid"`
	StartedAt        time.Time   `json:"startedAt"`
	EndedAt          *time.Time  `json:"endedAt"`
	DurationSeconds  *float64    `json:"durationSeconds"`
	ParticipantCount *int64      `json:"participantCount"`
	EndReason        *string     `json:"endReason"`
	Spans            *[]span     `json:"spans"`
	Turns            *[]callTurn `json:"turns"`
	Usage            *usage      `json:"usage,omitempty"`
	EndState         string      `json:"endState"`
	Tags             []string    `json:"tags"`
	Archived         bool        `json:"archived"`
	Clocks           []clock     `json:"clocks"`
}

type span struct {
	Name       string            `json:"name"`
	SpanID     string            `json:"spanId"`
	ParentID   *string           `json:"parentId"`
	StartMs    *float64          `json:"startMs"`
	DurationMs *float64          `json:"durationMs"`
	Attrs      map[string]string `json:"attrs"`
	ClockID    string            `json:"clockId"`
}

type callTurn struct {
	UserText             *string  `json:"userText"`
	AgentText            *string  `json:"agentText"`
	Model                *string  `json:"model"`
	LLMMs                *float64 `json:"llmMs"`
	TTFTMs               *float64 `json:"ttftMs"`
	STTMs                *float64 `json:"sttMs"`
	TTSMs                *float64 `json:"ttsMs"`
	EndOfTurnMs          *float64 `json:"endOfTurnMs"`
	InputTokens          *int64   `json:"inputTokens"`
	OutputTokens         *int64   `json:"outputTokens"`
	AudioSeconds         *float64 `json:"audioSeconds"`
	Characters           *float64 `json:"characters"`
	TranscriptionDelayMs *float64 `json:"transcriptionDelayMs"`
	E2EMs                *float64 `json:"e2eMs"`
	EOTProbability       *float64 `json:"eotProbability"`
	EOTThreshold         *float64 `json:"eotThreshold"`
	Interrupted          *bool    `json:"interrupted"`
	ClockID              string   `json:"clockId"`
}

type usage struct {
	LLMInputTokens  *int64   `json:"llmInputTokens"`
	LLMOutputTokens *int64   `json:"llmOutputTokens"`
	TTSCharacters   *float64 `json:"ttsCharacters"`
	STTSeconds      *float64 `json:"sttSeconds"`
	Note            string   `json:"note"`
}

type clock struct {
	ID        string     `json:"id"`
	Origin    string     `json:"origin"`
	OriginAt  *time.Time `json:"originAt"`
	Offset    *float64   `json:"offsetToCallStartSeconds"`
	Alignment string     `json:"alignment"`
}

// Clock ids and the alignment of a clock to the call's start.
const (
	clockCall  = "call"
	clockTrace = "trace"

	alignObserved = "observed"
	alignInferred = "inferred"
	alignUnknown  = "unknown"
)

// The span attribute on which LiveKit Agents 1.8.7 reports the room's name, on job_entrypoint and
// agent_session; "pii" because the agent can redact it.
const attrRoomName = "lk.pii.room_name"

// getCall serves operationId getCall.
func (a *API) getCall(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	org, project, id := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "callId")
	if _, err := a.Projects.Project(ctx, org, project); err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	c, err := a.Calls.Call(ctx, org, project, id)
	if err != nil {
		a.fail(w, r, err, "call not found")
		return
	}
	spans, err := a.Traces.CallSpans(ctx, org, project, c.ID)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	built, err := a.Traces.CallTurns(ctx, org, project, c.ID)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	body, ok := buildCall(c, spans, built)
	if !ok {
		// Neither a webhook nor a span named the room or reported a start: the contract requires
		// both, and nothing stored can fill them.
		writeError(w, r, http.StatusNotFound, codeNotFound, "call not found")
		return
	}
	writeJSON(w, http.StatusOK, body)
}

// fail answers a storage error: not_found for a missing record, internal_error otherwise. The
// storage error's text is logged, never sent.
func (a *API) fail(w http.ResponseWriter, r *http.Request, err error, notFound string) {
	if errors.Is(err, store.ErrNotFound) && notFound != "" {
		writeError(w, r, http.StatusNotFound, codeNotFound, notFound)
		return
	}
	a.Log.Error("api: read failed", "path", r.URL.Path, "err", err)
	writeError(w, r, http.StatusInternalServerError, codeInternal, "the call could not be read")
}

// buildCall maps a stored call to the contract. It reports false when the call lacks a room or a
// start that any source reported.
func buildCall(c store.Call, spans []store.Span, built turn.Result) (call, bool) {
	room := c.RoomName
	if room == "" {
		room = spanRoomName(spans)
	}
	traceStart := earliestStart(spans)
	started, startObserved := c.StartedAt, c.StartedAt != nil
	if started == nil {
		started = traceStart
	}
	if room == "" || started == nil {
		return call{}, false
	}

	out := call{
		ID:        c.ID,
		Room:      room,
		RoomSID:   text(c.RoomSID),
		StartedAt: *started,
		EndedAt:   c.EndedAt,
		EndState:  c.EndState,
		Tags:      []string{},
		Clocks:    clocks(*started, startObserved, traceStart),
	}
	if c.EndedAt != nil && startObserved {
		d := c.EndedAt.Sub(*started).Seconds()
		out.DurationSeconds = &d
	}
	if c.Participants > 0 {
		n := c.Participants
		out.ParticipantCount = &n
	}
	if len(spans) > 0 {
		s := toSpans(spans, traceStart)
		out.Spans = &s
	}
	if len(built.Turns) > 0 {
		t := toTurns(built.Turns)
		out.Turns = &t
	}
	out.Usage = toUsage(built.Usage)
	return out, true
}

// clocks returns the call clock, anchored at startedAt, and the trace clock, anchored at the
// earliest span. A start taken from the spans themselves is inferred, and so is the trace's
// offset to it.
func clocks(started time.Time, startObserved bool, traceStart *time.Time) []clock {
	align := alignInferred
	if startObserved {
		align = alignObserved
	}
	zero := 0.0
	out := []clock{{ID: clockCall, Origin: "call_start", OriginAt: &started, Offset: &zero, Alignment: align}}
	trace := clock{ID: clockTrace, Origin: "trace_start", Alignment: alignUnknown}
	if traceStart != nil {
		offset := traceStart.Sub(started).Seconds()
		trace.OriginAt, trace.Offset, trace.Alignment = traceStart, &offset, align
	}
	return append(out, trace)
}

func toSpans(spans []store.Span, origin *time.Time) []span {
	out := make([]span, 0, len(spans))
	for _, s := range spans {
		sp := span{Name: s.Name, SpanID: s.SpanID, ParentID: text(s.ParentSpanID), Attrs: s.Attributes, ClockID: clockTrace}
		if origin != nil && !s.Start.IsZero() {
			ms := float64(s.Start.Sub(*origin)) / float64(time.Millisecond)
			sp.StartMs = &ms
		}
		if !s.Start.IsZero() && !s.End.IsZero() {
			ms := float64(s.End.Sub(s.Start)) / float64(time.Millisecond)
			sp.DurationMs = &ms
		}
		out = append(out, sp)
	}
	return out
}

func toTurns(turns []turn.Turn) []callTurn {
	out := make([]callTurn, 0, len(turns))
	for _, t := range turns {
		out = append(out, callTurn{
			UserText:             t.UserText,
			AgentText:            t.AgentText,
			Model:                t.LLMModel,
			TTFTMs:               t.TTFTMs,
			TTSMs:                t.TTSMs,
			EndOfTurnMs:          t.EndOfTurnMs,
			InputTokens:          complete(t.InputTokens),
			OutputTokens:         complete(t.OutputTokens),
			Characters:           asFloat(complete(t.TTSCharacters)),
			TranscriptionDelayMs: t.TranscriptionDelayMs,
			E2EMs:                t.E2EMs,
			Interrupted:          t.Interrupted,
			ClockID:              clockTrace,
		})
	}
	return out
}

// toUsage maps the call's usage; nil when nothing reported any of it. A total some requests did
// not report is null, and the note says how many.
func toUsage(u turn.CallUsage) *usage {
	if u.InputTokens.Sum == nil && u.OutputTokens.Sum == nil && u.TTSCharacters.Sum == nil && u.STTAudioSeconds.Sum == nil {
		return nil
	}
	out := &usage{
		LLMInputTokens:  complete(u.InputTokens),
		LLMOutputTokens: complete(u.OutputTokens),
		TTSCharacters:   asFloat(complete(u.TTSCharacters)),
		STTSeconds:      complete(u.STTAudioSeconds),
	}
	notes := []string{
		"LLM tokens summed from the llm_request spans (gen_ai.usage, else lk.llm_metrics).",
		"TTS characters summed from the tts_request spans (lk.tts_metrics).",
		"STT seconds summed from the agent's lk.agents.usage.stt_audio_duration counter.",
	}
	for _, m := range []struct {
		what    string
		missing int
	}{
		{"LLM input tokens", u.InputTokens.Missing},
		{"LLM output tokens", u.OutputTokens.Missing},
		{"TTS characters", u.TTSCharacters.Missing},
	} {
		if m.missing > 0 {
			notes = append(notes, fmt.Sprintf("%s left null: %d %s did not report them.", m.what, m.missing, plural(m.missing, "request", "requests")))
		}
	}
	out.Note = strings.Join(notes, " ")
	return out
}

// complete is a total only when every request reported it.
func complete[T int64 | float64](t turn.Total[T]) *T {
	if t.Missing > 0 {
		return nil
	}
	return t.Sum
}

func asFloat(v *int64) *float64 {
	if v == nil {
		return nil
	}
	f := float64(*v)
	return &f
}

func text(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

// spanRoomName is the room name the agent's own spans report, empty when none does.
func spanRoomName(spans []store.Span) string {
	for _, s := range spans {
		if s.Name == "job_entrypoint" || s.Name == "agent_session" {
			if name := s.Attributes[attrRoomName]; name != "" {
				return name
			}
		}
	}
	return ""
}

// earliestStart is the earliest reported span start, nil when no span reported one.
func earliestStart(spans []store.Span) *time.Time {
	var out *time.Time
	for _, s := range spans {
		if s.Start.IsZero() {
			continue
		}
		if out == nil || s.Start.Before(*out) {
			t := s.Start
			out = &t
		}
	}
	return out
}
