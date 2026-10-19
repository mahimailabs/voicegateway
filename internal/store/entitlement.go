package store

import (
	"errors"
	"time"
)

// ErrEntitlementRevision marks a stale update or conflicting replay.
var ErrEntitlementRevision = errors.New("entitlement revision conflict")

// Entitlement is an operator-issued allowance for one explicit billing period.
// Prices and payment-provider identities never enter the instance.
type Entitlement struct {
	From         time.Time `json:"from"`
	To           time.Time `json:"to"`
	LimitMinutes int64     `json:"limitMinutes"`
	Active       bool      `json:"active"`
	Revision     int64     `json:"revision"`
}

// EntitlementUsage survives deletion of the underlying telemetry.
type EntitlementUsage struct {
	Entitlement
	CallSeconds     int64 `json:"callSeconds"`
	UnmeasuredCalls int64 `json:"unmeasuredCalls"`
	OpenCalls       int64 `json:"openCalls"`
}
