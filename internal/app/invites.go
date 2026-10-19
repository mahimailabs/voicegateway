package app

import (
	"context"
	"log/slog"

	"github.com/mahimailabs/voicegateway/ee/instance"
	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/invite"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// inviter is the invite service as the API calls it.
type inviter struct{ s *invite.Service }

func (i inviter) Invite(ctx context.Context, organisationID, email, role string, by *string, baseURL string) (store.Invitation, string, bool, error) {
	c, err := i.s.Create(ctx, invite.Invite{OrganisationID: organisationID, Email: email, Role: role, InvitedBy: by, BaseURL: baseURL})
	return c.Invitation, c.Link, c.Emailed, err
}

func (i inviter) ResetLink(ctx context.Context, organisationID, userID string, by *string, baseURL string) (store.Invitation, string, bool, error) {
	c, err := i.s.ResetLink(ctx, organisationID, userID, by, baseURL)
	return c.Invitation, c.Link, c.Emailed, err
}

// NewInviter is the invite service as the API calls it; tests wire it as web does.
func NewInviter(s *invite.Service) api.Inviter { return inviter{s} }

// NewInstanceAPI is the instance API's handlers (phase 06e, ee/instance) over an organisation store
// and 06d's invitations, with baseURL, VOICEGATEWAY_PUBLIC_URL, as every link's base. Web wires it
// only when VOICEGATEWAY_INSTANCE_TOKEN is set; tests wire it as web does.
func NewInstanceAPI(orgs instance.Store, links *invite.Service, baseURL string, log *slog.Logger) api.InstanceHandlers {
	return (&instance.Handlers{Store: orgs, Invites: links, BaseURL: baseURL, Log: log}).API()
}
