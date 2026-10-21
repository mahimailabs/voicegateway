package mcp

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// A contract operation is a tool only when a project key may call it, the router serves it and it
// answers JSON; organisation and project come from the key, never from the agent.
func TestToolsFollowTheContract(t *testing.T) {
	contract := []byte(`openapi: 3.1.1
info: {title: t, version: 9.9.9}
paths:
  /api/v1/org/{orgId}/project/{projectId}/calls/{callId}:
    get:
      operationId: getCall
      security: [{consoleSession: []}, {projectKey: []}]
      parameters:
        - {name: orgId, in: path, required: true, schema: {type: string}}
        - {name: projectId, in: path, required: true, schema: {type: string}}
        - {name: callId, in: path, required: true, schema: {type: string, minLength: 1}}
        - {name: tag, in: query, schema: {type: array, items: {type: string}}}
      responses: {"200": {description: ok, content: {application/json: {schema: {type: object}}}}}
  /api/v1/org/{orgId}/members:
    get:
      operationId: getMembers
      security: [{consoleSession: []}]
      responses: {"200": {description: ok, content: {application/json: {schema: {type: object}}}}}
  /api/v1/org/{orgId}/project/{projectId}/live-events:
    get:
      operationId: getLiveEvents
      security: [{projectKey: []}]
      responses: {"200": {description: ok, content: {text/event-stream: {schema: {type: string}}}}}
  /api/v1/org/{orgId}/project/{projectId}/later:
    get:
      operationId: getLater
      security: [{projectKey: []}]
      responses: {"200": {description: ok, content: {application/json: {schema: {type: object}}}}}
components:
  securitySchemes:
    consoleSession: {type: apiKey, in: cookie, name: vg_session}
    projectKey: {type: http, scheme: bearer}
`)
	r := chi.NewRouter()
	for _, p := range []string{"/api/v1/org/{orgId}/project/{projectId}/calls/{callId}", "/api/v1/org/{orgId}/members", "/api/v1/org/{orgId}/project/{projectId}/live-events"} {
		r.Get(p, func(http.ResponseWriter, *http.Request) {})
	}
	ts, version, err := tools(contract, r)
	if err != nil {
		t.Fatal(err)
	}
	if len(ts) != 1 || ts[0].name != "getCall" || version != "9.9.9" {
		t.Fatalf("tools = %+v, version %q; want getCall only, 9.9.9", ts, version)
	}
	in := ts[0].input
	if _, ok := in.Properties["orgId"]; ok {
		t.Error("orgId is a tool argument")
	}
	if len(in.Required) != 1 || in.Required[0] != "callId" || in.Properties["callId"].MinLength == nil {
		t.Errorf("input = %+v, want callId required with its contract schema", in)
	}

	target, err := ts[0].target(&store.KeyScope{OrganisationID: "o 1", ProjectID: "p/1"}, map[string]any{
		"callId": "a/b", "tag": []any{"x y", "z"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if want := "/api/v1/org/o%201/project/p%2F1/calls/a%2Fb?tag=x+y&tag=z"; target != want {
		t.Errorf("target = %s, want %s", target, want)
	}
	if _, err := ts[0].target(&store.KeyScope{}, map[string]any{"callId": []any{"a", "b"}}); err == nil {
		t.Error("two values for one path parameter were accepted")
	}
}

// /mcp admits a project key only: no credential, and a signed-in console user, are 401 with the
// Bearer challenge, before MCP reads the request.
func TestMCPNeedsAProjectKey(t *testing.T) {
	reached := false
	h := requireKey(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { reached = true }))
	for _, tt := range []struct {
		name string
		p    *store.Principal
		want int
	}{
		{"no credential", nil, http.StatusUnauthorized},
		{"console user", &store.Principal{UserID: "u", Roles: map[string]string{"o": "admin"}}, http.StatusUnauthorized},
		{"instance token", &store.Principal{Instance: true}, http.StatusUnauthorized},
		{"project key", &store.Principal{Key: &store.KeyScope{KeyID: "k", OrganisationID: "o", ProjectID: "p"}}, http.StatusOK},
	} {
		t.Run(tt.name, func(t *testing.T) {
			reached = false
			req := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader("{}"))
			if tt.p != nil {
				req = req.WithContext(store.WithPrincipal(req.Context(), *tt.p))
			}
			rec := httptest.NewRecorder()
			h.ServeHTTP(rec, req)
			if rec.Code != tt.want || reached != (tt.want == http.StatusOK) {
				t.Errorf("status %d, reached %v; want %d", rec.Code, reached, tt.want)
			}
			if tt.want == http.StatusUnauthorized && !strings.HasPrefix(rec.Header().Get("WWW-Authenticate"), "Bearer") {
				t.Error("no Bearer challenge")
			}
		})
	}
}
