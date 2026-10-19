package api

import (
	"net/http"
	"strings"
)

// InstanceHandlers are the instance API's operations (phase 06e, decision 0028). They are
// Enterprise code in ee/instance; internal/app sets them only when VOICEGATEWAY_INSTANCE_TOKEN is.
type InstanceHandlers struct {
	PutEntitlement               http.HandlerFunc
	GetEntitlement               http.HandlerFunc
	PutOrganisation              http.HandlerFunc
	GetOrganisation              http.HandlerFunc
	CreateOrganisationInvitation http.HandlerFunc
	GetOrganisationUsage         http.HandlerFunc
}

func (h InstanceHandlers) on() bool {
	return h.PutOrganisation != nil && h.GetOrganisation != nil && h.CreateOrganisationInvitation != nil &&
		h.GetOrganisationUsage != nil
}

// isInstancePath reports whether a request is for the instance API.
func isInstancePath(r *http.Request) bool {
	return r.URL.Path == "/api/v1/instance" || strings.HasPrefix(r.URL.Path, "/api/v1/instance/")
}

// guard runs after the gate. With the instance API off, its paths answer 404 to everyone, as if they
// did not exist. With it on, the gate's answers stand: a Bearer that matches no credential is no
// credential and gets 401 with the challenge, and allow refuses a session or a project key with 403.
func (h InstanceHandlers) guard(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !h.on() && isInstancePath(r) {
			writeError(w, r, http.StatusNotFound, codeNotFound, "no such API path")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// optionalEntitlement is absent on self-hosted installs, including the member-facing read.
func optionalEntitlement(h http.HandlerFunc) http.HandlerFunc {
	if h != nil {
		return h
	}
	return func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no entitlement API")
	}
}
