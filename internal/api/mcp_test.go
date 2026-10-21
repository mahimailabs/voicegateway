package api

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/alexedwards/scs/v2/memstore"
	sdk "github.com/modelcontextprotocol/go-sdk/mcp"

	contractdoc "github.com/mahimailabs/voicegateway/docs/api"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/mcp"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// bearer adds a project key to every request an MCP client sends.
type bearer struct{ key string }

func (b bearer) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	r.Header.Set("Authorization", "Bearer "+b.key)
	return http.DefaultTransport.RoundTrip(r)
}

// mcpSession connects an MCP client to srv's /mcp with key, or with no key when key is "".
func mcpSession(t *testing.T, url, key string) (*sdk.ClientSession, error) {
	t.Helper()
	client := sdk.NewClient(&sdk.Implementation{Name: "test", Version: "1"}, nil)
	hc := http.DefaultClient
	if key != "" {
		hc = &http.Client{Transport: bearer{key}}
	}
	return client.Connect(context.Background(), &sdk.StreamableClientTransport{Endpoint: url + "/mcp", HTTPClient: hc}, nil)
}

// The MCP server (plan task dev-mcp): every tool reads the key's project through the /api/v1
// router and answers what the contract says that operation answers; a refusal reaches the agent
// as the API's error, and a key that stops being valid stops every tool.
func TestMCPToolsReadTheKeysProject(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.storeRealCall("api/testdata/full-call.webhooks.jsonl", "api/testdata/full-call.spans.jsonl", "api/testdata/full-call.session_report.json",
		"join/testdata/agents-1.8.7-metrics-3.binpb", "join/testdata/agents-1.8.7-metrics-4.binpb")
	f.summarize()
	callID := f.callID("RM_35ayuuwAkwVj")
	other := f.addProject(t.Name() + "-other")
	f.storeLayerReads(f.project)

	keyID, sum, key, err := auth.NewKey()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.CreateProjectKey(ctx, "default", f.project, keyID, "mcp", sum, ""); err != nil {
		t.Fatal(err)
	}
	secure, plain := auth.NewSessions(memstore.New())
	gate := (&auth.Service{Users: f.pg, Secure: secure, Plain: plain, Log: f.log, Now: time.Now}).Gate

	// The production wiring (internal/app): the router the tools call has no gate of its own; the
	// gate in front of /mcp identified the caller.
	inner := &API{
		Projects: f.pg, Settings: f.pg, Metadata: f.pg, Issues: f.pg, Calls: f.pg, Traces: f.events, Layers: f.events,
		Endpoints: f.pg, Accounts: f.pg, Stats: f.pg, ResourceEndpoints: f.pg, ResourceSamples: f.events,
		Log: f.log, Now: time.Now,
	}
	routes, ok := inner.Routes().(mcp.API)
	if !ok {
		t.Fatal("Routes is not a chi router")
	}
	server, err := mcp.New(contractdoc.OpenAPI, routes, f.log)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(server.Handler(gate))
	t.Cleanup(srv.Close)

	session, err := mcpSession(t, srv.URL, key)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = session.Close() }()

	listed, err := session.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, tl := range listed.Tools {
		names = append(names, tl.Name)
		if tl.Annotations == nil || !tl.Annotations.ReadOnlyHint {
			t.Errorf("%s is not marked read-only", tl.Name)
		}
		if props, _ := json.Marshal(tl.InputSchema); strings.Contains(string(props), `"orgId"`) || strings.Contains(string(props), `"projectId"`) {
			t.Errorf("%s asks for the organisation or project, which come from the key: %s", tl.Name, props)
		}
	}
	slices.Sort(names)
	want := []string{
		"getCall", "getCallMetadata", "getCallStats", "getCallTags", "getCloudAgents", "getEgress", "getIngress",
		"getIssueMetadata", "getMediaServer", "getProjectConnection", "getProjectSettings", "getResources", "getSip", "getWorkers",
		"getWorkspaces", "listCalls", "listIssues",
	}
	if !slices.Equal(names, want) {
		t.Fatalf("tools = %v, want %v", names, want)
	}

	call := func(t *testing.T, s *sdk.ClientSession, name string, args map[string]any) (*sdk.CallToolResult, error) {
		t.Helper()
		return s.CallTool(ctx, &sdk.CallToolParams{Name: name, Arguments: args})
	}
	text := func(r *sdk.CallToolResult) string {
		var b strings.Builder
		for _, c := range r.Content {
			if tc, ok := c.(*sdk.TextContent); ok {
				b.WriteString(tc.Text)
			}
		}
		return b.String()
	}

	// The issue a tool reads by id comes from listIssues, as an agent would find it.
	issues, err := call(t, session, "listIssues", map[string]any{"from": "2026-10-17T00:00:00Z", "to": "2026-10-18T00:00:00Z"})
	if err != nil || issues.IsError {
		t.Fatalf("listIssues: %v %s", err, text(issues))
	}
	var issueList struct {
		Items []struct {
			ID string `json:"id"`
		} `json:"items"`
	}
	if err := json.Unmarshal([]byte(text(issues)), &issueList); err != nil || len(issueList.Items) == 0 {
		t.Fatalf("listIssues answered %s (%v), want the stored call's issues", text(issues), err)
	}

	// A second key, for a project with no calls whose layers were never read.
	otherID, otherSum, otherKey, err := auth.NewKey()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.CreateProjectKey(ctx, "default", other, otherID, "mcp", otherSum, ""); err != nil {
		t.Fatal(err)
	}
	otherSession, err := mcpSession(t, srv.URL, otherKey)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = otherSession.Close() }()

	// Each tool's happy path, and a refusal or failure the API answers for the same tool: through
	// the other project's key (another project's call or issue is not found; a layer never read is
	// source_unavailable), or with arguments the API refuses.
	type failure struct {
		other  bool
		args   map[string]any
		status string
	}
	issueID := issueList.Items[0].ID
	day := map[string]any{"from": "2026-10-17T00:00:00Z", "to": "2026-10-18T00:00:00Z"}
	backwards := map[string]any{"from": "2026-10-18T00:00:00Z", "to": "2026-10-17T00:00:00Z"}
	neverRead := &failure{other: true, status: "503"}
	cases := []struct {
		tool, path string
		args       map[string]any
		check      func(t *testing.T, body string)
		fail       *failure
	}{
		{"getWorkspaces", "", nil, func(t *testing.T, body string) {
			if !strings.Contains(body, `"`+f.project+`"`) || strings.Contains(body, `"`+other+`"`) {
				t.Errorf("workspaces = %s, want the key's project only", body)
			}
		}, nil},
		{"listCalls", "calls", map[string]any{"limit": 5, "channel": []any{"web", "phone", "console", "local", "unknown"}}, func(t *testing.T, body string) {
			if !strings.Contains(body, callID) {
				t.Errorf("listCalls = %s, want call %s", body, callID)
			}
		}, &failure{args: map[string]any{"cursor": "not-a-cursor"}, status: "400"}},
		{"getCall", "calls/" + callID, map[string]any{"callId": callID}, func(t *testing.T, body string) {
			if !strings.Contains(body, `"vg-full-134152"`) {
				t.Errorf("getCall = %s, want room vg-full-134152", body)
			}
		}, &failure{other: true, args: map[string]any{"callId": callID}, status: "404"}},
		{"getCallMetadata", "calls/" + callID + "/metadata", map[string]any{"callId": callID}, nil, &failure{other: true, args: map[string]any{"callId": callID}, status: "404"}},
		{"getCallStats", "call-stats", day, nil, &failure{args: backwards, status: "400"}},
		{"getCallTags", "call-tags", nil, nil, nil},
		{"listIssues", "issues", day, nil, &failure{args: backwards, status: "400"}},
		{"getIssueMetadata", "issues/" + issueID + "/metadata", map[string]any{"issueId": issueID}, nil, &failure{other: true, args: map[string]any{"issueId": issueID}, status: "404"}},
		{"getProjectSettings", "settings", nil, nil, nil},
		{"getMediaServer", "media-server", nil, nil, neverRead},
		{"getSip", "sip", nil, nil, neverRead},
		{"getEgress", "egress", nil, nil, neverRead},
		{"getIngress", "ingress", nil, nil, neverRead},
		{"getCloudAgents", "agents", nil, nil, neverRead},
		{"getWorkers", "workers", nil, nil, nil}, // no worker endpoint listed is an answer, not a failure
		{"getResources", "resources", nil, nil, nil},
	}
	for _, tt := range cases {
		t.Run(tt.tool, func(t *testing.T) {
			res, err := call(t, session, tt.tool, tt.args)
			if err != nil {
				t.Fatal(err)
			}
			if res.IsError {
				t.Fatalf("%s failed: %s", tt.tool, text(res))
			}
			if !json.Valid([]byte(text(res))) {
				t.Fatalf("%s answered no JSON: %s", tt.tool, text(res))
			}
			f.validate(t, tt.path, text(res))
			if tt.check != nil {
				tt.check(t, text(res))
			}
			if tt.fail == nil {
				return
			}
			s := session
			if tt.fail.other {
				s = otherSession
			}
			res, err = call(t, s, tt.tool, tt.fail.args)
			if err != nil {
				t.Fatal(err)
			}
			if !res.IsError || !strings.HasPrefix(text(res), tt.fail.status+" ") {
				t.Errorf("%s (other project %v) with %v = %s, want an error result with %s", tt.tool, tt.fail.other, tt.fail.args, text(res), tt.fail.status)
			}
		})
	}

	// Arguments outside the contract's schema never reach the API.
	if res, err := call(t, session, "listCalls", map[string]any{"limit": 500}); err == nil && !res.IsError {
		t.Errorf("listCalls with limit 500 = %s, want refused", text(res))
	}

	// A connection with no key, or a session cookie's user, is refused before MCP reads it.
	if _, err := mcpSession(t, srv.URL, ""); err == nil {
		t.Error("an MCP client with no key connected")
	}
	if _, err := mcpSession(t, srv.URL, key[:len(key)-1]+map[bool]string{true: "B", false: "A"}[key[len(key)-1] == 'A']); err == nil {
		t.Error("an MCP client with a wrong key connected")
	}
	resp, err := http.Post(srv.URL+"/mcp", "application/json", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`))
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized || !strings.HasPrefix(resp.Header.Get("WWW-Authenticate"), "Bearer") {
		t.Errorf("no key = %d %q, want 401 with the Bearer challenge", resp.StatusCode, resp.Header.Get("WWW-Authenticate"))
	}

	// Revoking the key stops every tool on the next call: each call passes the gate again.
	if err := f.pg.RevokeProjectKey(ctx, "default", f.project, keyID); err != nil {
		t.Fatal(err)
	}
	for _, tt := range cases {
		if res, err := call(t, session, tt.tool, tt.args); err == nil {
			t.Errorf("%s after revoke = %s, want refused", tt.tool, text(res))
		}
	}
	// The other key still reads: revoking one key leaves the rest.
	if res, err := call(t, otherSession, "getWorkspaces", nil); err != nil || res.IsError || !strings.Contains(text(res), `"`+other+`"`) {
		t.Errorf("the other key after revoke = %v %v", res, err)
	}
}

// validate checks a tool's answer against the contract's 200 response for its operation, at path
// under the project, or at /api/v1/workspaces when path is empty.
func (f *fixture) validate(t *testing.T, path, body string) {
	t.Helper()
	target := "/api/v1/workspaces"
	if path != "" {
		target = "/api/v1/org/default/project/" + f.project + "/" + path
	}
	f.validateResponse(t, httptest.NewRequest(http.MethodGet, target, nil), http.StatusOK, http.Header{"Content-Type": {"application/json"}}, []byte(body))
}

// storeLayerReads stores one successful read of every LiveKit layer for project, as the worker's
// scrapers do, so each layer's read answers.
func (f *fixture) storeLayerReads(project string) {
	f.t.Helper()
	ctx := context.Background()
	read := func(layer string) store.LayerRead {
		return store.LayerRead{OrganisationID: "default", ProjectID: project, Layer: layer, ReadAt: time.Now(), OK: true, Every: 5 * time.Second}
	}
	errs := []error{
		f.events.StoreRead(ctx, read(store.LayerMediaServer), nil, nil),
		f.events.StoreSIPRead(ctx, read(store.LayerSIP), &store.SIP{LiveKitNumbers: []store.SIPNumber{}, Inbound: []store.SIPInbound{}, Outbound: []store.SIPOutbound{}, Rules: []store.SIPRule{}}),
		f.events.StoreEgressRead(ctx, read(store.LayerEgress), nil),
		f.events.StoreIngressRead(ctx, read(store.LayerIngress), nil),
		f.events.StoreCloudAgentsRead(ctx, read(store.LayerCloudAgents), &store.CloudAgents{}),
		f.events.StoreWorkerRead(ctx, read(store.LayerWorkers), nil, nil),
		f.events.StoreResourceRead(ctx, read(store.LayerResources), nil, nil),
	}
	if err := errors.Join(errs...); err != nil {
		f.t.Fatal(err)
	}
}
