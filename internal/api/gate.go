package api

import (
	"crypto/sha256"
	"crypto/subtle"
	"net/http"
	"strings"
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
				writeError(w, r, http.StatusUnauthorized, codeUnauthenticated, "the API is off until VOICEGATEWAY_API_TOKEN is set")
				return
			}
			got, ok := bearer(r.Header.Get("Authorization"))
			sum := sha256.Sum256([]byte(got))
			if !ok || subtle.ConstantTimeCompare(sum[:], want[:]) != 1 {
				writeError(w, r, http.StatusUnauthorized, codeUnauthenticated, "a valid API token is required")
				return
			}
			next.ServeHTTP(w, r)
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
