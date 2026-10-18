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
