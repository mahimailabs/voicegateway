package api

import (
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// access is a route's minimum organisation role (decision 0019) and what it is scoped to: a
// project, an organisation, the signed-in principal alone, or the instance (phase 06e), whose
// routes take the instance token and nothing else, at the role Instance.
type access struct {
	min   string
	scope string
}

const (
	scopeProject      = "project"
	scopeOrganisation = "organisation"
	scopePrincipal    = "principal"
	scopeInstance     = "instance"
)

// routeAccess is every /api/v1 route's access, by method and chi pattern. Routes registers a route
// only through this table, and TestEveryRouteHasAMinimumRole compares it with the contract's
// x-access.minimumRole.
var routeAccess = map[string]access{
	"GET /api/v1/org/{orgId}/project/{projectId}/issues":                    {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/issues/{issueId}/metadata": {"Viewer", scopeProject},
	"PUT /api/v1/org/{orgId}/project/{projectId}/issues/{issueId}/metadata": {"Member", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/call-tags":                 {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/calls/{callId}/metadata":   {"Viewer", scopeProject},
	"PUT /api/v1/org/{orgId}/project/{projectId}/calls/{callId}/metadata":   {"Member", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/settings":                  {"Viewer", scopeProject},
	"PUT /api/v1/org/{orgId}/project/{projectId}/settings":                  {"Member", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/live-events":               {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/entitlement":                                   {"Viewer", scopeOrganisation},
	"GET /api/v1/instance/organisations/{orgId}/entitlement":                {"Instance", scopeInstance},
	"PUT /api/v1/instance/organisations/{orgId}/entitlement":                {"Instance", scopeInstance},
	"GET /api/v1/workspaces":                                                {"Viewer", scopePrincipal},
	"GET /api/v1/org/{orgId}/project/{projectId}/calls":                     {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/calls/{callId}":            {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/call-stats":                {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/media-server":              {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/sip":                       {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/egress":                    {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/ingress":                   {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/agents":                    {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/resources":                 {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/workers":                   {"Viewer", scopeProject},
	"GET /api/v1/org/{orgId}/project/{projectId}/keys":                      {"Admin", scopeProject},
	"POST /api/v1/org/{orgId}/project/{projectId}/keys":                     {"Admin", scopeProject},
	"DELETE /api/v1/org/{orgId}/project/{projectId}/keys/{keyId}":           {"Admin", scopeProject},
	"POST /api/v1/org/{orgId}/projects":                                     {"Admin", scopeOrganisation},
	"GET /api/v1/org/{orgId}/members":                                       {"Viewer", scopeOrganisation},
	"PATCH /api/v1/org/{orgId}/members/{userId}":                            {"Admin", scopeOrganisation},
	"DELETE /api/v1/org/{orgId}/members/{userId}":                           {"Admin", scopeOrganisation},
	"POST /api/v1/org/{orgId}/members/{userId}/password-reset":              {"Admin", scopeOrganisation},
	"GET /api/v1/org/{orgId}/invitations":                                   {"Admin", scopeOrganisation},
	"POST /api/v1/org/{orgId}/invitations":                                  {"Admin", scopeOrganisation},
	"DELETE /api/v1/org/{orgId}/invitations/{invitationId}":                 {"Admin", scopeOrganisation},
	"PUT /api/v1/instance/organisations/{orgId}":                            {"Instance", scopeInstance},
	"GET /api/v1/instance/organisations/{orgId}":                            {"Instance", scopeInstance},
	"POST /api/v1/instance/organisations/{orgId}/invitations":               {"Instance", scopeInstance},
	"GET /api/v1/instance/organisations/{orgId}/usage":                      {"Instance", scopeInstance},
}

// roleRank orders the contract's role names, and the stored ones, Viewer < Member < Admin; an
// unknown role ranks 0.
func roleRank(role string) int {
	switch strings.ToLower(role) {
	case "viewer":
		return 1
	case "member":
		return 2
	case "admin":
		return 3
	}
	return 0
}

// contractRole maps a stored role, admin, member or viewer, to the contract's name.
func contractRole(stored string) string {
	switch stored {
	case "admin":
		return "Admin"
	case "member":
		return "Member"
	case "viewer":
		return "Viewer"
	}
	return stored
}

// storedRole maps a role from a request to the stored name, or "" when it is not one.
func storedRole(role string) string {
	switch role {
	case "Admin", "Member", "Viewer":
		return strings.ToLower(role)
	}
	return ""
}

// handle registers one route behind its access from routeAccess. A route missing from the table
// answers 500, never openly, and TestEveryRouteHasAMinimumRole names it.
func handle(r chi.Router, method, pattern string, h http.HandlerFunc) {
	acc, ok := routeAccess[method+" "+pattern]
	if !ok {
		r.Method(method, pattern, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			writeError(w, r, http.StatusInternalServerError, codeInternal, "this route has no minimum role")
		}))
		return
	}
	r.Method(method, pattern, allow(acc, h))
}

// keyMay reports whether a project API key may call a route at all: the signed-in principal's own
// routes, and a project's reads open to a Viewer. The contract's projectKey security names the same
// operations (TestProjectKeySecurityMatchesTheGate).
func keyMay(acc access) bool {
	return acc.scope == scopePrincipal || acc.scope == scopeProject && roleRank(acc.min) <= roleRank("Viewer")
}

// allow answers 403 a caller whose organisation role is below a route's minimum. A user outside
// the organisation in the path is told so. A project API key names no user: it is a read-only
// Viewer of its one project, and is refused every other project and every organisation operation.
// The instance token calls the instance routes and nothing else, and nothing else calls them.
func allow(acc access, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p, _ := store.PrincipalFrom(r.Context())
		if p.Instance != (acc.scope == scopeInstance) {
			if p.Instance {
				writeError(w, r, http.StatusForbidden, codeForbidden, "the instance token calls the instance API only")
			} else {
				writeError(w, r, http.StatusForbidden, codeForbidden, "this needs the instance token")
			}
			return
		}
		if acc.scope == scopePrincipal || acc.scope == scopeInstance {
			next.ServeHTTP(w, r)
			return
		}
		if k := p.Key; k != nil {
			own := k.OrganisationID == chi.URLParam(r, "orgId") && k.ProjectID == chi.URLParam(r, "projectId")
			if !keyMay(acc) || !own {
				writeError(w, r, http.StatusForbidden, codeForbidden, "this API key reads its own project only")
				return
			}
			next.ServeHTTP(w, r)
			return
		}
		role := p.Roles[chi.URLParam(r, "orgId")]
		if role == "" {
			writeError(w, r, http.StatusForbidden, codeForbidden, "you are not a member of this organisation")
			return
		}
		if roleRank(role) < roleRank(acc.min) {
			writeError(w, r, http.StatusForbidden, codeForbidden, "this needs the "+acc.min+" role")
			return
		}
		next.ServeHTTP(w, r)
	})
}
