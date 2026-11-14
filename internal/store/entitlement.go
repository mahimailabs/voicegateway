package store

import (
	"errors"
	"time"
)

// ErrEntitlementRevision marks a stale update or conflicting replay.
var ErrEntitlementRevision = errors.New("entitlement revision conflict")

// ErrPlanLimit refuses a project or invitation past the organisation's plan (decision 0042).
var ErrPlanLimit = errors.New("plan limit reached")

// Entitlement is an operator-issued allowance for one explicit billing period, in credits: one
// profiled call minute is one credit (decision 0042). A nil limit is unlimited. Prices and
// payment-provider identities never enter the instance.
type Entitlement struct {
	From          time.Time `json:"from"`
	To            time.Time `json:"to"`
	LimitCredits  int64     `json:"limitCredits"`
	RetentionDays *int32    `json:"retentionDays"`
	MaxProjects   *int32    `json:"maxProjects"`
	MaxMembers    *int32    `json:"maxMembers"`
	Active        bool      `json:"active"`
	Revision      int64     `json:"revision"`
}

// SameLimits reports whether e and o set the same period, credits, limits and state.
func (e Entitlement) SameLimits(o Entitlement) bool {
	eq := func(a, b *int32) bool { return (a == nil) == (b == nil) && (a == nil || *a == *b) }
	return e.From.Equal(o.From) && e.To.Equal(o.To) && e.LimitCredits == o.LimitCredits && e.Active == o.Active &&
		eq(e.RetentionDays, o.RetentionDays) && eq(e.MaxProjects, o.MaxProjects) && eq(e.MaxMembers, o.MaxMembers)
}

// RetentionPlan is an organisation whose plan keeps call data for Days days.
type RetentionPlan struct {
	OrganisationID string
	Days           int32
}

// EntitlementUsage survives deletion of the underlying telemetry.
type EntitlementUsage struct {
	Entitlement
	CallSeconds     int64 `json:"callSeconds"`
	UnmeasuredCalls int64 `json:"unmeasuredCalls"`
	OpenCalls       int64 `json:"openCalls"`
}
