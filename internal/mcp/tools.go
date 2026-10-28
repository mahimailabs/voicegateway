package mcp

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/jsonschema-go/jsonschema"
	"github.com/pb33f/libopenapi"
	"github.com/pb33f/libopenapi/datamodel/high/base"
	v3 "github.com/pb33f/libopenapi/datamodel/high/v3"
)

// tool is one contract operation served as an MCP tool.
type tool struct {
	name, path, description string
	// params are the operation's path and query parameters other than orgId and projectId, which
	// come from the key.
	params []param
	input  *jsonschema.Schema
}

type param struct {
	name, in string
}

// scoped are the path parameters every project route takes, filled from the caller's key.
var scoped = map[string]bool{"orgId": true, "projectId": true}

// tools reads the contract's operations a project API key may call (security projectKey) that the
// router serves and that answer JSON: the reads a coding agent can use. A server-sent event stream
// or a recording's audio is no tool answer. It returns the contract's version with them.
func tools(contract []byte, routes chi.Routes) ([]tool, string, error) {
	doc, err := libopenapi.NewDocument(contract)
	if err != nil {
		return nil, "", fmt.Errorf("mcp: read the contract: %w", err)
	}
	model, err := doc.BuildV3Model()
	if err != nil {
		return nil, "", fmt.Errorf("mcp: build the contract: %w", err)
	}
	var out []tool
	for path, item := range model.Model.Paths.PathItems.FromOldest() {
		op := item.Get
		if op == nil || !keyMay(op) || !answersJSON(op) || !served(routes, path) {
			continue
		}
		t := tool{name: op.OperationId, path: path, description: describe(op)}
		t.input = &jsonschema.Schema{Type: "object", Properties: map[string]*jsonschema.Schema{}}
		for _, p := range append(item.Parameters, op.Parameters...) {
			if (p.In != "path" && p.In != "query") || p.In == "path" && scoped[p.Name] {
				continue
			}
			s, err := paramSchema(p)
			if err != nil {
				return nil, "", fmt.Errorf("mcp: %s parameter %s: %w", op.OperationId, p.Name, err)
			}
			t.input.Properties[p.Name] = s
			if p.In == "path" || p.Required != nil && *p.Required {
				t.input.Required = append(t.input.Required, p.Name)
			}
			t.params = append(t.params, param{name: p.Name, in: p.In})
		}
		out = append(out, t)
	}
	if len(out) == 0 {
		return nil, "", fmt.Errorf("mcp: the contract has no operation a project key may call")
	}
	return out, model.Model.Info.Version, nil
}

func keyMay(op *v3.Operation) bool {
	for _, req := range op.Security {
		if _, ok := req.Requirements.Get("projectKey"); ok {
			return true
		}
	}
	return false
}

func answersJSON(op *v3.Operation) bool {
	if op.Responses == nil {
		return false
	}
	ok, found := op.Responses.Codes.Get("200")
	if !found || ok.Content == nil {
		return false
	}
	_, isJSON := ok.Content.Get("application/json")
	return isJSON && ok.Content.Len() == 1
}

// served reports whether the router serves GET on a contract path; the contract also names
// operations a later phase serves.
func served(routes chi.Routes, path string) bool {
	return routes.Match(chi.NewRouteContext(), http.MethodGet, path)
}

func describe(op *v3.Operation) string {
	parts := make([]string, 0, 2)
	for _, s := range []string{op.Summary, op.Description} {
		if s = strings.TrimSpace(s); s != "" {
			parts = append(parts, s)
		}
	}
	return strings.Join(parts, "\n\n")
}

// paramSchema is a parameter's contract schema as the tool's JSON Schema, with its description.
func paramSchema(p *v3.Parameter) (*jsonschema.Schema, error) {
	s := &jsonschema.Schema{Type: "string"}
	if p.Schema != nil {
		var raw *base.Schema
		if raw = p.Schema.Schema(); raw == nil {
			return nil, fmt.Errorf("unresolved schema: %w", p.Schema.GetBuildError())
		}
		b, err := raw.MarshalJSONInline()
		if err != nil {
			return nil, err
		}
		if err := json.Unmarshal(b, s); err != nil {
			return nil, err
		}
	}
	if s.Description == "" {
		s.Description = p.Description
	}
	return s, nil
}
