package api

import (
	"errors"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type sip struct {
	ReadAt         time.Time           `json:"readAt"`
	Source         string              `json:"source"`
	LiveKitNumbers []store.SIPNumber   `json:"livekitNumbers"`
	Inbound        []store.SIPInbound  `json:"inbound"`
	Outbound       []store.SIPOutbound `json:"outbound"`
	Rules          []store.SIPRule     `json:"rules"`
	SourceStatus   sourceStatus        `json:"sourceStatus"`
}

const (
	sipSource = "Read by the worker with the project's API key: SIPService ListSIPInboundTrunk, " +
		"ListSIPOutboundTrunk and ListSIPDispatchRule, and PhoneNumberService ListPhoneNumbers. " +
		"Usernames, passwords, PINs, header values, room prefixes and names are dropped before " +
		"storage; numbers keep their last 4 digits. LiveKit keeps no call history, so calls are " +
		"not listed here."
	sipNoNumbers = " This LiveKit server has no phone number service, so LiveKit numbers do not apply."
)

func (a *API) sip(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(ctx, org, proj); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, codeNotFound, "no such project")
			return
		}
		a.internal(w, r, "find project", err)
		return
	}
	st, err := a.Layers.LayerStatus(ctx, org, proj, store.LayerSIP)
	if err != nil {
		a.internal(w, r, "sip status", err)
		return
	}
	unavailable := func() {
		msg := unavailableMessage(st)
		switch {
		case st.ErrorClass == store.ErrorPermissionDenied:
			msg = "the project's API key has no SIP admin grant"
		case st.LastGoodReadAt != nil:
			msg = "no SIP read is kept from the last 30 days"
		}
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, msg)
	}
	if st.LastGoodReadAt == nil {
		unavailable()
		return
	}
	got, err := a.Layers.LatestSIP(ctx, org, proj)
	if errors.Is(err, store.ErrNotFound) {
		// The good reads have expired.
		unavailable()
		return
	}
	if err != nil {
		a.internal(w, r, "sip read", err)
		return
	}
	source := sipSource
	if !got.SIP.PhoneNumbers {
		source += sipNoNumbers
	}
	writeJSON(w, http.StatusOK, sip{
		ReadAt:         got.ReadAt,
		Source:         source,
		LiveKitNumbers: got.SIP.LiveKitNumbers,
		Inbound:        got.SIP.Inbound,
		Outbound:       got.SIP.Outbound,
		Rules:          got.SIP.Rules,
		SourceStatus:   statusOf(st),
	})
}
