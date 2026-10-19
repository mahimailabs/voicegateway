package scrape

import (
	"context"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Verifier checks endpoint connectivity with a signed ListRooms read. A caller-controlled
// server can accept any token, so this is not proof of ownership or tenant isolation.
type Verifier struct {
	Dial Dial
	// Timeout bounds the one read.
	Timeout time.Duration
}

// NewVerifier verifies through the worker's own address guard (NewWorkerClient): with publicOnly, a
// URL that resolves to a private, loopback, link-local or metadata address is never dialed.
func NewVerifier(publicOnly bool) Verifier {
	return Verifier{Dial: DialThrough(NewWorkerClient(publicOnly)), Timeout: 5 * time.Second}
}

// VerifyLiveKit returns nil when LiveKit answered the signed read. LiveKit refusing the token is
// store.ErrLiveKitSecretInvalid; anything else, a refused address included, is
// store.ErrLiveKitUnreachable, so the answer never says which addresses the guard refuses. The
// cause is wrapped for the command line; it never holds the secret, which travels only inside the
// signed token.
func (v Verifier) VerifyLiveKit(ctx context.Context, url, apiKey, apiSecret string) error {
	ctx, cancel := context.WithTimeout(ctx, v.Timeout)
	defer cancel()
	_, err := v.Dial(url, apiKey, apiSecret).ListRooms(ctx)
	if err == nil {
		return nil
	}
	switch classify(err) {
	case store.ErrorUnauthenticated, store.ErrorPermissionDenied:
		return fmt.Errorf("%w: %w", store.ErrLiveKitSecretInvalid, err)
	}
	return fmt.Errorf("%w: %w", store.ErrLiveKitUnreachable, err)
}
