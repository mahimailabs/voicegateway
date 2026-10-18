package app

import (
	"context"

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
