package scrape

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
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

// failure says where a LiveKit read failed without logging LiveKit's message: the Twirp code, the
// HTTP status of a reply that was not a Twirp error, the Twirp client step that failed (its own text,
// such as "failed to unmarshal proto response", present only when it wraps a cause) and the type of
// the innermost cause, such as *tls.CertificateVerificationError. classify keeps only the class.
func failure(err error) slog.Attr {
	attrs := make([]slog.Attr, 0, 4)
	var te twirp.Error
	if errors.As(err, &te) {
		attrs = append(attrs, slog.String("twirp_code", string(te.Code())))
		if status := te.Meta("status_code"); status != "" {
			attrs = append(attrs, slog.String("http_status", status))
		}
		if errors.Unwrap(te) != nil {
			attrs = append(attrs, slog.String("client_step", te.Msg()))
		}
	}
	attrs = append(attrs, slog.String("cause", fmt.Sprintf("%T", innermost(err))))
	return slog.Attr{Key: "failure", Value: slog.GroupValue(attrs...)}
}

// innermost follows err's wrapping to its first cause. Of a join, such as call's deadline wrap
// "%w: %w", it follows the last error, which is the call's own.
func innermost(err error) error {
	for {
		switch u := err.(type) {
		case interface{ Unwrap() error }:
			next := u.Unwrap()
			if next == nil {
				return err
			}
			err = next
		case interface{ Unwrap() []error }:
			errs := u.Unwrap()
			if len(errs) == 0 {
				return err
			}
			err = errs[len(errs)-1]
		default:
			return err
		}
	}
}
