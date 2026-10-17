package api

import (
	"crypto/sha256"
	"crypto/subtle"
	"net/http"
	"strings"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// BearerToken is the interim gate on /api/v1 until the sign-in decision (#321) lands: a request
// must carry "Authorization: Bearer <token>". With no token configured it answers 401 to every
// request rather than serve transcripts openly. Tokens are compared as SHA-256 digests in constant
// time, so neither their content nor their length leaks through timing.
func BearerToken(token string) func(http.Handler) http.Handler {
	want := sha256.Sum256([]byte(token))
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if token == "" {
				unauthenticated(w, r, "the API is off until VOICEGATEWAY_API_TOKEN is set")
				return
			}
			got, ok := bearer(r.Header.Get("Authorization"))
			sum := sha256.Sum256([]byte(got))
			if !ok || subtle.ConstantTimeCompare(sum[:], want[:]) != 1 {
				unauthenticated(w, r, "a valid API token is required")
				return
			}
			next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), store.Principal{Token: true})))
		})
	}
}

// bearer returns the credentials of a Bearer Authorization header. The scheme name is
// case-insensitive (RFC 7235); the token is everything after one space.
func bearer(header string) (string, bool) {
	const scheme = "bearer "
	if len(header) <= len(scheme) || !strings.EqualFold(header[:len(scheme)], scheme) {
		return "", false
	}
	return header[len(scheme):], true
}

// unauthenticated answers 401 with the challenge RFC 6750 asks of a Bearer-protected resource.
func unauthenticated(w http.ResponseWriter, r *http.Request, message string) {
	w.Header().Set("WWW-Authenticate", `Bearer realm="voicegateway"`)
	writeError(w, r, http.StatusUnauthorized, codeUnauthenticated, message)
}
