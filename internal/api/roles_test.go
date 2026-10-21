package api

import (
	"net/http"
	"os"
	"slices"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/pb33f/libopenapi"
)

// Acceptance check 2 of phase 06b: every /api/v1 route has a minimum role in routeAccess, and it
// is the minimumRole its operation declares in the contract's x-access.
func TestEveryRouteHasAMinimumRole(t *testing.T) {
	spec, err := os.ReadFile("../../docs/api/openapi.yaml")
	if err != nil {
		t.Fatal(err)
	}
	doc, err := libopenapi.NewDocument(spec)
	if err != nil {
		t.Fatal(err)
	}
	model, err := doc.BuildV3Model()
	if err != nil {
		t.Fatal(err)
	}
	contract := map[string]string{}
	for path, item := range model.Model.Paths.PathItems.FromOldest() {
		for method, op := range item.GetOperations().FromOldest() {
			var x struct {
				MinimumRole string `yaml:"minimumRole"`
			}
			if node, ok := op.Extensions.Get("x-access"); ok {
				if err := node.Decode(&x); err != nil {
					t.Fatalf("%s %s: x-access: %v", method, path, err)
				}
			}
			contract[strings.ToUpper(method)+" "+path] = x.MinimumRole
		}
	}

	routes, ok := (&API{Gate: passThrough}).Routes().(chi.Routes)
	if !ok {
		t.Fatal("Routes is not a chi router")
	}
	var served []string
	err = chi.Walk(routes, func(method, route string, _ http.Handler, _ ...func(http.Handler) http.Handler) error {
		served = append(served, method+" "+route)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(served) == 0 {
		t.Fatal("no routes walked")
	}
	for _, r := range served {
		acc, ok := routeAccess[r]
		if !ok {
			t.Errorf("%s has no minimum role in routeAccess", r)
			continue
		}
		want, ok := contract[r]
		if !ok {
			t.Errorf("%s is served but not in the contract", r)
			continue
		}
		if acc.min != want {
			t.Errorf("%s: minimum role %s, contract says %s", r, acc.min, want)
		}
	}
	for r := range routeAccess {
		if !slices.Contains(served, r) {
			t.Errorf("routeAccess names %s, which is not served", r)
		}
	}
}

// The contract offers projectKey security on exactly the operations the gate lets a project API key
// call, so a generated client or the /docs runner never sends a key where it gets 403.
func TestProjectKeySecurityMatchesTheGate(t *testing.T) {
	spec, err := os.ReadFile("../../docs/api/openapi.yaml")
	if err != nil {
		t.Fatal(err)
	}
	doc, err := libopenapi.NewDocument(spec)
	if err != nil {
		t.Fatal(err)
	}
	model, err := doc.BuildV3Model()
	if err != nil {
		t.Fatal(err)
	}
	for path, item := range model.Model.Paths.PathItems.FromOldest() {
		for method, op := range item.GetOperations().FromOldest() {
			var x struct {
				MinimumRole string `yaml:"minimumRole"`
				Scope       string `yaml:"scope"`
			}
			node, ok := op.Extensions.Get("x-access")
			if !ok {
				t.Fatalf("%s %s has no x-access", method, path)
			}
			if err := node.Decode(&x); err != nil {
				t.Fatalf("%s %s: x-access: %v", method, path, err)
			}
			offered := false
			for _, req := range op.Security {
				if _, ok := req.Requirements.Get("projectKey"); ok {
					offered = true
				}
			}
			if want := keyMay(access{min: x.MinimumRole, scope: x.Scope}); offered != want {
				t.Errorf("%s %s (%s): projectKey offered %v, the gate allows a key %v", strings.ToUpper(method), path, op.OperationId, offered, want)
			}
		}
	}
}

// The widgets an embed token may read each route for (embedAccess) are the contract's
// x-embed-access widgets, and an operation offers the embedToken scheme exactly when it has them.
func TestEmbedAccessMatchesTheContract(t *testing.T) {
	spec, err := os.ReadFile("../../docs/api/openapi.yaml")
	if err != nil {
		t.Fatal(err)
	}
	doc, err := libopenapi.NewDocument(spec)
	if err != nil {
		t.Fatal(err)
	}
	model, err := doc.BuildV3Model()
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for path, item := range model.Model.Paths.PathItems.FromOldest() {
		for method, op := range item.GetOperations().FromOldest() {
			key := strings.ToUpper(method) + " " + path
			var x struct {
				Widgets []string `yaml:"widgets"`
			}
			if node, ok := op.Extensions.Get("x-embed-access"); ok {
				if err := node.Decode(&x); err != nil {
					t.Fatalf("%s: x-embed-access: %v", key, err)
				}
			}
			offered := false
			for _, req := range op.Security {
				if _, ok := req.Requirements.Get("embedToken"); ok {
					offered = true
				}
			}
			got := embedAccess[key]
			seen[key] = true
			if !slices.Equal(got, x.Widgets) || offered != (len(x.Widgets) > 0) {
				t.Errorf("%s (%s): gate widgets %v, contract widgets %v, embedToken offered %v", key, op.OperationId, got, x.Widgets, offered)
			}
		}
	}
	for key := range embedAccess {
		if !seen[key] {
			t.Errorf("%s: embed access for a route the contract does not have", key)
		}
	}
}
