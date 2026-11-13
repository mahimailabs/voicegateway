package api

import (
	"context"
	"errors"
	"net/http"
	"slices"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxAdminRows is how many organisations or accounts an admin list returns; total says how many
// there are in all.
const maxAdminRows = 500

// maxAdminUsageSpan is the longest period getAdminOrganisationUsage reads, the instance API's.
const maxAdminUsageSpan = 62 * 24 * time.Hour

// Admin reads the instance for a signed-in instance admin (decision 0041).
type Admin interface {
	AdminOrganisations(ctx context.Context, limit int) ([]store.AdminOrganisation, int64, error)
	AdminUsers(ctx context.Context, limit int) ([]store.AdminUser, int64, error)
	OrganisationUsage(ctx context.Context, id string, from, to time.Time) (store.Usage, error)
}

// adminOrganisations is the contract's AdminOrganisations.
type adminOrganisations struct {
	Items []adminOrganisation `json:"items"`
	Total int64               `json:"total"`
}

type adminOrganisation struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	CreatedAt time.Time `json:"createdAt"`
	Members   int64     `json:"members"`
	Admins    int64     `json:"admins"`
	Projects  int64     `json:"projects"`
}

// adminUsers is the contract's AdminUsers.
type adminUsers struct {
	Items []adminUser `json:"items"`
	Total int64       `json:"total"`
}

type adminUser struct {
	ID            string         `json:"id"`
	Email         string         `json:"email"`
	CreatedAt     time.Time      `json:"createdAt"`
	InstanceAdmin bool           `json:"instanceAdmin"`
	Organisations []adminUserOrg `json:"organisations"`
}

type adminUserOrg struct {
	ID   string `json:"id"`
	Role string `json:"role"`
}

// adminUsage is the contract's AdminUsage.
type adminUsage struct {
	OrganisationID      string    `json:"organisationId"`
	From                time.Time `json:"from"`
	To                  time.Time `json:"to"`
	CallSeconds         int64     `json:"callSeconds"`
	Calls               int64     `json:"calls"`
	InferredCalls       int64     `json:"inferredCalls"`
	InferredCallSeconds int64     `json:"inferredCallSeconds"`
	UnendedCalls        int64     `json:"unendedCalls"`
	UnmeasuredCalls     int64     `json:"unmeasuredCalls"`
}

func (a *API) adminOff(w http.ResponseWriter, r *http.Request) bool {
	if a.Admin == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "the admin reads are not available on this server")
		return true
	}
	return false
}

// listAdminOrganisations serves operationId listAdminOrganisations: the newest organisations with
// their counts.
func (a *API) listAdminOrganisations(w http.ResponseWriter, r *http.Request) {
	if a.adminOff(w, r) {
		return
	}
	orgs, total, err := a.Admin.AdminOrganisations(r.Context(), maxAdminRows)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	out := adminOrganisations{Items: make([]adminOrganisation, 0, len(orgs)), Total: total}
	for _, o := range orgs {
		out.Items = append(out.Items, adminOrganisation{ID: o.ID, Name: o.Name, CreatedAt: o.CreatedAt, Members: o.Members, Admins: o.Admins, Projects: o.Projects})
	}
	writeJSON(w, http.StatusOK, out)
}

// listAdminUsers serves operationId listAdminUsers: the newest accounts with their organisation
// roles, and never a credential.
func (a *API) listAdminUsers(w http.ResponseWriter, r *http.Request) {
	if a.adminOff(w, r) {
		return
	}
	users, total, err := a.Admin.AdminUsers(r.Context(), maxAdminRows)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	out := adminUsers{Items: make([]adminUser, 0, len(users)), Total: total}
	for _, u := range users {
		item := adminUser{ID: u.ID, Email: u.Email, CreatedAt: u.CreatedAt, InstanceAdmin: u.InstanceAdmin, Organisations: make([]adminUserOrg, 0, len(u.Roles))}
		for org, role := range u.Roles {
			item.Organisations = append(item.Organisations, adminUserOrg{ID: org, Role: contractRole(role)})
		}
		slices.SortFunc(item.Organisations, func(x, y adminUserOrg) int { return strings.Compare(x.ID, y.ID) })
		out.Items = append(out.Items, item)
	}
	writeJSON(w, http.StatusOK, out)
}

// getAdminOrganisationUsage serves operationId getAdminOrganisationUsage: one organisation's
// profiled call time in a period, by the rules of decision 0029.
func (a *API) getAdminOrganisationUsage(w http.ResponseWriter, r *http.Request) {
	if a.adminOff(w, r) {
		return
	}
	id := chi.URLParam(r, "orgId")
	q := r.URL.Query()
	from, errFrom := time.Parse(time.RFC3339, q.Get("from"))
	to, errTo := time.Parse(time.RFC3339, q.Get("to"))
	if errFrom != nil || errTo != nil || !to.After(from) || to.Sub(from) > maxAdminUsageSpan {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "from and to must be RFC 3339 instants, to after from, at most 62 days apart")
		return
	}
	u, err := a.Admin.OrganisationUsage(r.Context(), id, from, to)
	if errors.Is(err, store.ErrNotFound) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such organisation")
		return
	}
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	writeJSON(w, http.StatusOK, adminUsage{
		OrganisationID: id, From: from.UTC(), To: to.UTC(),
		CallSeconds: u.CallSeconds, Calls: u.Calls, InferredCalls: u.InferredCalls, InferredCallSeconds: u.InferredCallSeconds,
		UnendedCalls: u.UnendedCalls, UnmeasuredCalls: u.UnmeasuredCalls,
	})
}
