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
// projects. The interim token names no user, so it is 401 here.
func (a *API) getWorkspaces(w http.ResponseWriter, r *http.Request) {
	p, _ := store.PrincipalFrom(r.Context())
	if p.UserID == "" {
		unauthenticated(w, r, "workspaces need a signed-in user")
		return
	}
	ws, err := a.Accounts.Workspaces(r.Context(), p.UserID)
	if err != nil {
		a.fail(w, r, err, "")
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
