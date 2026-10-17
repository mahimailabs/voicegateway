package turn

import (
	"cmp"
	"maps"
	"slices"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Pipelines a call's spans can show.
const (
	PipelineCascaded = "cascaded"
	PipelineRealtime = "realtime"
	PipelineText     = "text"
	PipelineUnknown  = "unknown"
)

const (
	attrAgentName = "lk.agent_name"    // ATTR_AGENT_NAME
	attrRoomName  = "lk.pii.room_name" // the room's name, on job_entrypoint and agent_session
)

// Summary is the per-call figures listCalls reads (phase 04d). A nil value is unknown.
type Summary struct {
	// TurnCount is the number of agent_turn spans; nil when the call has none.
	TurnCount *int
	// AnswerMs are the turns' measured end-to-end times in turn order; nil when no turn measured
	// one. AnswerMsMedian is their median as the console takes it, the upper middle value.
	AnswerMs       []float64
	AnswerMsMedian *float64
	// Models are the model names the spans report, most used first.
	Models   []string
	Pipeline string
	// AgentKey is the agent name most of the call's spans carry; nil when none does.
	AgentKey *string
	// RoomName is the room name the agent reported on job_entrypoint or agent_session, and
	// FirstSeenAt the earliest span start; empty and nil when none did.
	RoomName    string
	FirstSeenAt *time.Time
}

// Summarize builds a call's summary from its spans and the turns built from them.
func Summarize(spans []store.Span, res Result) Summary {
	s := Summary{Models: []string{}, Pipeline: pipeline(spans)}
	if len(res.Turns) > 0 {
		n := len(res.Turns)
		s.TurnCount = &n
	}
	for _, t := range res.Turns {
		if t.E2EMs != nil {
			s.AnswerMs = append(s.AnswerMs, *t.E2EMs)
		}
	}
	s.AnswerMsMedian = Median(s.AnswerMs)

	models, agents := map[string]int{}, map[string]int{}
	for _, sp := range spans {
		if s.RoomName == "" && (sp.Name == "job_entrypoint" || sp.Name == "agent_session") {
			s.RoomName = sp.Attributes[attrRoomName]
		}
		if !sp.Start.IsZero() && (s.FirstSeenAt == nil || sp.Start.Before(*s.FirstSeenAt)) {
			t := sp.Start
			s.FirstSeenAt = &t
		}
		if m := sp.Attributes[attrRequestModel]; m != "" {
			models[m]++
		}
		if a := sp.Attributes[attrAgentName]; a != "" {
			agents[a]++
		}
	}
	s.Models = byCount(models)
	if top := byCount(agents); len(top) > 0 {
		s.AgentKey = &top[0]
	}
	return s
}

// Median is the console's median: the upper middle value of the sorted values, nil when there are
// none (dashboard/src/mocks/calls-query.ts).
func Median(values []float64) *float64 {
	if len(values) == 0 {
		return nil
	}
	sorted := slices.Sorted(slices.Values(values))
	m := sorted[len(sorted)/2]
	return &m
}

// byCount returns the keys most counted first, ties by name.
func byCount(counts map[string]int) []string {
	keys := slices.Collect(maps.Keys(counts))
	slices.SortFunc(keys, func(a, b string) int {
		return cmp.Or(cmp.Compare(counts[b], counts[a]), cmp.Compare(a, b))
	})
	if keys == nil {
		return []string{}
	}
	return keys
}

// pipeline says how the agent produced speech: a realtime model, cascaded LLM and TTS, or text
// alone; unknown when the spans show none of them.
func pipeline(spans []store.Span) string {
	var llm, tts, realtime bool
	for _, sp := range spans {
		switch sp.Name {
		case spanLLMNode:
			llm = true
		case spanTTSNode:
			tts = true
		case spanRealtime:
			realtime = true
		}
	}
	switch {
	case realtime:
		return PipelineRealtime
	case llm && tts:
		return PipelineCascaded
	case llm:
		return PipelineText
	}
	return PipelineUnknown
}
