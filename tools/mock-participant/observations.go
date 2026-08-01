package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// Reporter posts what a call actually did to VoiceGateway.
//
// Fire and forget by design: the endpoint answers 202 and queues, and this
// worker sits on the SIP answer path, so a slow collector must never become call
// latency. Reporting happens after the call ends, never during it.
type Reporter struct {
	// BaseURL is the VoiceGateway server, e.g. http://collector.example.invalid:8080.
	// Empty disables reporting entirely, which is the default: a load run that
	// nobody is collecting is a legitimate way to use this tool.
	BaseURL string
	// APIKey is sent as a bearer token. The /v1/calls router requires the write
	// scope, so an unauthenticated post is a 401 rather than a silent drop.
	APIKey string
	// Client is exposed for tests. nil uses a client with a short timeout.
	Client *http.Client
	// Project scopes the row, when the deployment uses projects.
	Project string
	Logf    func(string, ...any)
}

// legObservation mirrors LegObservation in
// src/voicegateway/server/api/call_observations.py.
//
// The Python model sets extra="forbid", so a field this struct sends that the
// model does not declare is a 422 for the WHOLE report, not a dropped key. Every
// name here was read off that model rather than guessed, and omitempty keeps an
// unmeasured value out of the payload instead of sending a zero that would read
// as a measurement.
type legObservation struct {
	ParticipantSID      string `json:"participant_sid"`
	Identity            string `json:"identity,omitempty"`
	Kind                string `json:"kind,omitempty"`
	JoinedAtMs          int64  `json:"joined_at_ms,omitempty"`
	LeftAtMs            int64  `json:"left_at_ms,omitempty"`
	DisconnectReason    string `json:"disconnect_reason,omitempty"`
	FirstAudioTrackAtMs int64  `json:"first_audio_track_at_ms,omitempty"`
	AudioTrackSID       string `json:"audio_track_sid,omitempty"`
	AudioCodec          string `json:"audio_codec,omitempty"`
}

// callObservation mirrors CallObservation in the same module.
type callObservation struct {
	// "loadgen", always. The endpoint accepts agent | loadgen and REFUSES
	// webhook, because only the signature-verified receiver may claim to be one.
	Origin      string           `json:"origin"`
	RoomName    string           `json:"room_name,omitempty"`
	RunID       string           `json:"run_id,omitempty"`
	Project     string           `json:"project,omitempty"`
	StartedAtMs int64            `json:"started_at_ms,omitempty"`
	EndedAtMs   int64            `json:"ended_at_ms,omitempty"`
	Legs        []legObservation `json:"legs,omitempty"`
}

// Enabled reports whether anything will actually be sent.
func (r *Reporter) Enabled() bool { return r != nil && strings.TrimSpace(r.BaseURL) != "" }

func (r *Reporter) logf(format string, args ...any) {
	if r != nil && r.Logf != nil {
		r.Logf(format, args...)
	}
}

// Report posts one call's observation.
//
// Called after the call has ended, so its cost is off the answer path entirely.
// A failure here is logged and swallowed: losing an observation is a gap in
// evidence, while failing the call over it would be a gap in the run.
func (r *Reporter) Report(
	ctx context.Context, a Assignment, stats *CallStats, participantSID, runID string,
) error {
	if !r.Enabled() {
		return nil
	}
	leg := legObservation{
		ParticipantSID: participantSID,
		Identity:       "mock-participant-" + a.JobID,
		// This worker joins as the agent side of the call. The kind vocabulary
		// is a closed Literal in the Python model.
		Kind: "AGENT",
	}
	// Only send what was actually observed. A zero here would be an epoch
	// timestamp in 1970 presented as a measurement.
	if t := stats.JoinedAt(); !t.IsZero() {
		leg.JoinedAtMs = t.UnixMilli()
	}
	// The first RTP packet IS the first audio: this leg is the receiver, and
	// this timestamp is one of the two inputs the server uses to derive answer
	// latency, so it is the single most valuable thing this worker can report.
	if t := stats.FirstPacketAt(); !t.IsZero() {
		leg.FirstAudioTrackAtMs = t.UnixMilli()
	}

	body := callObservation{
		Origin:   "loadgen",
		RoomName: a.RoomName,
		RunID:    runID,
		Project:  r.Project,
		Legs:     []legObservation{leg},
	}
	if t := stats.JoinedAt(); !t.IsZero() {
		body.StartedAtMs = t.UnixMilli()
	}

	payload, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshal observation: %w", err)
	}
	endpoint := strings.TrimSuffix(r.BaseURL, "/") + "/v1/calls/observations"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if r.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+r.APIKey)
	}

	client := r.Client
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("post observation: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode == http.StatusUnprocessableEntity {
		// 422 means this struct and the Python model disagree about the shape.
		// Said plainly, because the alternative is a silent evidence gap that
		// only shows up as an empty report at the end of a run.
		return fmt.Errorf(
			"observation rejected as malformed (422): the payload does not match "+
				"CallObservation, which sets extra=forbid. endpoint %s", endpoint,
		)
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return fmt.Errorf(
			"observation rejected (%d): /v1/calls requires the write scope, so the "+
				"api key must carry it", resp.StatusCode,
		)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("observation rejected (%d)", resp.StatusCode)
	}
	return nil
}
