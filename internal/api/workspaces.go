package api

import (
	"net/http"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// workspaces is the contract's Workspaces.
type workspaces struct {
	Items []workspace `json:"items"`
}

type workspace struct {
	ID       string             `json:"id"`
	Name     string             `json:"name"`
	Projects []workspaceProject `json:"projects"`
}

type workspaceProject struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// getWorkspaces serves operationId getWorkspaces: the signed-in user's organisations and their
// projects; for a project key, its organisation with that one project.
func (a *API) getWorkspaces(w http.ResponseWriter, r *http.Request) {
	p, _ := store.PrincipalFrom(r.Context())
	var ws []store.Workspace
	switch {
	case p.Key != nil:
		// A project key sees its organisation with its one project, so a client finds its scope.
		w1, err := a.Accounts.KeyWorkspace(r.Context(), p.Key.OrganisationID, p.Key.ProjectID)
		if err != nil {
			a.fail(w, r, err, "")
			return
		}
		ws = []store.Workspace{w1}
	case p.UserID != "":
		var err error
		if ws, err = a.Accounts.Workspaces(r.Context(), p.UserID); err != nil {
			a.fail(w, r, err, "")
			return
		}
	default:
		unauthenticated(w, r, "workspaces need a signed-in user or a project key")
		return
	}
	out := workspaces{Items: make([]workspace, 0, len(ws))}
	for _, x := range ws {
		item := workspace{ID: x.ID, Name: x.Name, Projects: make([]workspaceProject, 0, len(x.Projects))}
		for _, pr := range x.Projects {
			item.Projects = append(item.Projects, workspaceProject{ID: pr.ID, Name: pr.Name})
		}
		out.Items = append(out.Items, item)
	}
	writeJSON(w, http.StatusOK, out)
}
