package scrape

import (
	"context"
	"errors"
	"net"

	"github.com/twitchtv/twirp"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// classify names why a LiveKit read failed, from the Twirp error the SDK returns (server-sdk-go's
// ServerError is twirp.Error). The class is stored; LiveKit's message is not.
func classify(err error) string {
	var te twirp.Error
	if errors.As(err, &te) {
		switch te.Code() {
		case twirp.Unauthenticated:
			return store.ErrorUnauthenticated
		case twirp.PermissionDenied:
			return store.ErrorPermissionDenied
		case twirp.Unimplemented, twirp.BadRoute:
			return store.ErrorNotSupported
		case twirp.Unavailable, twirp.DeadlineExceeded:
			return store.ErrorUnreachable
		case twirp.Internal, twirp.Unknown:
			// Twirp wraps transport failures (DNS, refused connections) as internal errors.
			var ne net.Error
			if errors.As(err, &ne) || errors.Is(err, context.DeadlineExceeded) {
				return store.ErrorUnreachable
			}
		}
		return store.ErrorOther
	}
	var ne net.Error
	if errors.As(err, &ne) || errors.Is(err, context.DeadlineExceeded) {
		return store.ErrorUnreachable
	}
	return store.ErrorOther
}

// isNotFound reports whether LiveKit said the thing read is gone, such as a room that ended between
// listing it and reading its participants.
func isNotFound(err error) bool {
	var te twirp.Error
	return errors.As(err, &te) && te.Code() == twirp.NotFound
}
