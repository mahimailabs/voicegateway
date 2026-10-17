package api

import "net/http"

// unauthenticated answers 401 with the challenge RFC 6750 asks of a Bearer-protected resource.
func unauthenticated(w http.ResponseWriter, r *http.Request, message string) {
	w.Header().Set("WWW-Authenticate", `Bearer realm="voicegateway"`)
	writeError(w, r, http.StatusUnauthorized, codeUnauthenticated, message)
}
