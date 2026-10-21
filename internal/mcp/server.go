package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// API is the /api/v1 router the tools call, wired with no gate of its own: the caller's
// principal, put on the request by the gate in front of /mcp, travels with each call, and the
// router's role table refuses what the key may not read.
type API interface {
	http.Handler
	chi.Routes
}

// Server is the MCP server: one tool per contract operation a project key may call.
type Server struct {
	server *mcp.Server
	api    API
	log    *slog.Logger
}

// New builds the server from the contract and the router its tools call. The server's version is
// the contract's.
func New(contract []byte, api API, log *slog.Logger) (*Server, error) {
	ts, version, err := tools(contract, api)
	if err != nil {
		return nil, err
	}
	s := &Server{api: api, log: log}
	s.server = mcp.NewServer(&mcp.Implementation{Name: "voicegateway", Title: "VoiceGateway", Version: version}, &mcp.ServerOptions{
		Instructions: "Reads one VoiceGateway project, the one the API key belongs to: its calls with their turns, " +
			"issues, and what VoiceGateway reads of each LiveKit layer. A value no source reported is null, never zero. " +
			"Every tool only reads.",
		Logger: log,
	})
	for _, t := range ts {
		mcp.AddTool(s.server, &mcp.Tool{
			Name:        t.name,
			Description: t.description,
			InputSchema: t.input,
			Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, IdempotentHint: true, OpenWorldHint: new(false)},
		}, s.call(t))
	}
	return s, nil
}

// Handler serves MCP over Streamable HTTP, statelessly, so any web replica answers any request.
// gate is the same gate as /api/v1 (auth.Service.Gate); a request it identified no project key on
// is refused before MCP reads it.
func (s *Server) Handler(gate func(http.Handler) http.Handler) http.Handler {
	h := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return s.server }, &mcp.StreamableHTTPOptions{
		Stateless:    true,
		JSONResponse: true,
		Logger:       s.log,
		// Tool arguments are a few ids and filters.
		MaxRequestBodyBytes: 64 << 10,
		// A caller that hangs up stops its reads.
		PropagateRequestCancellation: true,
	})
	return gate(requireKey(h))
}

// requireKey admits a project API key only. A console session cookie is not MCP's credential: a
// web page must not reach a signed-in user's data through it.
func requireKey(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p, ok := store.PrincipalFrom(r.Context())
		if !ok || p.Key == nil {
			w.Header().Set("WWW-Authenticate", `Bearer realm="voicegateway"`)
			writeError(w, http.StatusUnauthorized, "unauthenticated", "send a project API key as Authorization: Bearer vgk_...")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"code": code, "message": message})
}

// call is a tool's handler: it runs the tool's operation through the router as the caller, and
// hands back the API's answer, a refusal included.
func (s *Server) call(t tool) mcp.ToolHandlerFor[map[string]any, any] {
	return func(ctx context.Context, _ *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
		p, ok := store.PrincipalFrom(ctx)
		if !ok || p.Key == nil {
			return nil, nil, fmt.Errorf("mcp: no project key on the call")
		}
		target, err := t.target(p.Key, args)
		if err != nil {
			return nil, nil, err
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
		if err != nil {
			return nil, nil, fmt.Errorf("mcp: %s: %w", t.name, err)
		}
		req.Header.Set("Accept", "application/json")
		// The router answers in process; a recorder holds its answer, as one response body.
		rec := httptest.NewRecorder()
		s.api.ServeHTTP(rec, req)
		body := rec.Body.String()
		if rec.Code != http.StatusOK {
			return &mcp.CallToolResult{
				IsError: true,
				Content: []mcp.Content{&mcp.TextContent{Text: fmt.Sprintf("%d %s: %s", rec.Code, http.StatusText(rec.Code), body)}},
			}, nil, nil
		}
		return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: body}}}, nil, nil
	}
}

// target is the operation's URL for a key and the tool's arguments: the key's organisation and
// project in the path, path parameters escaped, query parameters repeated for an array.
func (t tool) target(k *store.KeyScope, args map[string]any) (string, error) {
	path := strings.NewReplacer("{orgId}", url.PathEscape(k.OrganisationID), "{projectId}", url.PathEscape(k.ProjectID)).Replace(t.path)
	q := url.Values{}
	for _, p := range t.params {
		v, ok := args[p.name]
		if !ok || v == nil {
			continue
		}
		vals, err := queryValues(v)
		if err != nil {
			return "", fmt.Errorf("mcp: %s argument %s: %w", t.name, p.name, err)
		}
		if p.in == "path" {
			if len(vals) != 1 {
				return "", fmt.Errorf("mcp: %s argument %s takes one value", t.name, p.name)
			}
			path = strings.Replace(path, "{"+p.name+"}", url.PathEscape(vals[0]), 1)
			continue
		}
		q[p.name] = vals
	}
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	return path, nil
}

// queryValues formats a JSON argument as the contract's form style: a scalar as itself, an array
// as one value per item.
func queryValues(v any) ([]string, error) {
	switch v := v.(type) {
	case string:
		return []string{v}, nil
	case bool:
		return []string{strconv.FormatBool(v)}, nil
	case float64:
		return []string{strconv.FormatFloat(v, 'f', -1, 64)}, nil
	case json.Number:
		return []string{v.String()}, nil
	case []any:
		out := make([]string, 0, len(v))
		for _, item := range v {
			vals, err := queryValues(item)
			if err != nil {
				return nil, err
			}
			if len(vals) != 1 {
				return nil, fmt.Errorf("nested array")
			}
			out = append(out, vals[0])
		}
		return out, nil
	}
	return nil, fmt.Errorf("unsupported value %T", v)
}
