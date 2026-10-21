package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// API serves the console's /api/v1 reads and its member writes.
type API struct {
	Issues            IssueMetadataStore
	Metadata          CallMetadataStore
	Settings          ProjectSettingsStore
	StorageBucket     string
	ResourceEndpoints ResourceEndpoints
	ResourceSamples   ResourceSamples
	Live              LiveChanges
	Projects          Projects
	Calls             Calls
	Traces            Traces
	Layers            Layers
	// Endpoints lists each project's worker metrics endpoints, from Postgres.
	Endpoints WorkerEndpoints
	Accounts  Accounts
	Stats     Stats
	Keys      Keys
	// Invitations lists and revokes invitations; Inviter makes them and reset links.
	Invitations Invitations
	Inviter     Inviter
	// NewProjects creates projects, their LiveKit secrets sealed: the store voicegateway project add
	// writes through.
	NewProjects ProjectCreator
	// VerifyLiveKit proves a new project's LiveKit secret before NewProjects stores it; without it
	// createProject is off.
	VerifyLiveKit LiveKitVerifier
	// LinkBase is an invitation link's base for a request: VOICEGATEWAY_PUBLIC_URL, or the
	// request's scheme and Host (auth.Service.LinkBase).
	LinkBase func(r *http.Request) string
	// NewKey draws a project key: its key_id, the SHA-256 of its secret, and the full key
	// (auth.NewKey).
	NewKey func() (keyID string, secretSHA256 []byte, full string, err error)
	Log    *slog.Logger
	Now    func() time.Time
	// Instance serves the instance API (phase 06e), from ee/instance; with no handler set it is
	// off, and every /api/v1/instance path answers 404 as if it did not exist.
	Instance InstanceHandlers
	// Gate runs before every /api/v1 route: auth.Service.Gate, which identifies a signed-in user or
	// the interim token (decision 0027).
	// Nil denies every request, so a router wired without a gate never serves openly.
	Gate func(http.Handler) http.Handler
}

// Routes returns the /api/v1 handler, to mount at the server's root.
func (a *API) Routes() http.Handler {
	r := chi.NewRouter()
	r.Use(requestID)
	// The gate runs on the whole router, so an unknown /api/v1 path is gated too; what is not a
	// route answers in the contract's Error shape.
	// The gate identifies the caller; requirePrincipal refuses any request it could not identify,
	// so a router wired without a gate serves nothing.
	if a.Gate != nil {
		r.Use(a.Gate)
	}
	r.Use(a.Instance.guard)
	r.Use(requirePrincipal)
	r.NotFound(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such API path")
	})
	r.MethodNotAllowed(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusMethodNotAllowed, codeInvalidRequest, "method not allowed")
	})
	// Every route goes through handle, which puts it behind its minimum role in routeAccess.
	const project = "/api/v1/org/{orgId}/project/{projectId}"
	handle(r, http.MethodGet, "/api/v1/workspaces", a.getWorkspaces)
	handle(r, http.MethodGet, project+"/settings", a.getProjectSettings)
	handle(r, http.MethodPut, project+"/settings", a.updateProjectSettings)
	handle(r, http.MethodGet, project+"/calls", a.listCalls)
	handle(r, http.MethodGet, project+"/call-tags", a.getCallTags)
	handle(r, http.MethodGet, project+"/issues", a.listIssues)
	handle(r, http.MethodGet, project+"/issues/{issueId}/metadata", a.getIssueMetadata)
	handle(r, http.MethodPut, project+"/issues/{issueId}/metadata", a.updateIssueMetadata)
	handle(r, http.MethodGet, project+"/calls/{callId}", a.getCall)
	handle(r, http.MethodGet, project+"/calls/{callId}/metadata", a.getCallMetadata)
	handle(r, http.MethodPut, project+"/calls/{callId}/metadata", a.updateCallMetadata)
	handle(r, http.MethodGet, project+"/call-stats", a.getCallStats)
	handle(r, http.MethodGet, project+"/media-server", a.mediaServer)
	handle(r, http.MethodGet, project+"/sip", a.sip)
	handle(r, http.MethodGet, project+"/egress", a.egress)
	handle(r, http.MethodGet, project+"/ingress", a.ingress)
	handle(r, http.MethodGet, project+"/agents", a.cloudAgents)
	handle(r, http.MethodGet, project+"/workers", a.workers)
	handle(r, http.MethodGet, project+"/resources", a.resources)
	handle(r, http.MethodGet, project+"/live-events", a.liveEvents)
	handle(r, http.MethodGet, project+"/keys", a.listProjectKeys)
	handle(r, http.MethodPost, project+"/keys", a.createProjectKey)
	handle(r, http.MethodDelete, project+"/keys/{keyId}", a.revokeProjectKey)
	handle(r, http.MethodPost, "/api/v1/org/{orgId}/projects", a.createProject)
	handle(r, http.MethodGet, "/api/v1/org/{orgId}/members", a.getMembers)
	handle(r, http.MethodPatch, "/api/v1/org/{orgId}/members/{userId}", a.updateMember)
	handle(r, http.MethodDelete, "/api/v1/org/{orgId}/members/{userId}", a.removeMember)
	handle(r, http.MethodPost, "/api/v1/org/{orgId}/members/{userId}/password-reset", a.createPasswordReset)
	handle(r, http.MethodGet, "/api/v1/org/{orgId}/invitations", a.getInvitations)
	handle(r, http.MethodPost, "/api/v1/org/{orgId}/invitations", a.createInvitation)
	handle(r, http.MethodDelete, "/api/v1/org/{orgId}/invitations/{invitationId}", a.revokeInvitation)
	handle(r, http.MethodGet, "/api/v1/org/{orgId}/entitlement", optionalEntitlement(a.Instance.GetEntitlement))
	const instance = "/api/v1/instance/organisations/{orgId}"
	handle(r, http.MethodPut, instance, a.Instance.PutOrganisation)
	handle(r, http.MethodGet, instance, a.Instance.GetOrganisation)
	handle(r, http.MethodPost, instance+"/invitations", a.Instance.CreateOrganisationInvitation)
	handle(r, http.MethodGet, instance+"/usage", a.Instance.GetOrganisationUsage)
	handle(r, http.MethodGet, instance+"/entitlement", optionalEntitlement(a.Instance.GetEntitlement))
	handle(r, http.MethodPut, instance+"/entitlement", optionalEntitlement(a.Instance.PutEntitlement))
	return r
}

// requirePrincipal answers 401, with the Bearer challenge, any request the gate found no caller on.
func requirePrincipal(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, ok := store.PrincipalFrom(r.Context()); !ok {
			unauthenticated(w, r, "sign in, or send a valid API key")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// requestID gives every request an id the server makes, for the Error body and the log. A client's
// X-Request-Id is ignored, so a caller cannot choose the id an operator searches the log for.
func requestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b [12]byte
		if _, err := rand.Read(b[:]); err != nil {
			writeJSON(w, http.StatusInternalServerError, apiError{Code: codeInternal, Message: "no request id"})
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), middleware.RequestIDKey, hex.EncodeToString(b[:]))))
	})
}
