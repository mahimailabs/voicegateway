package auth

import "net/http"

// ClientConfig is what the console learns at runtime, before anyone signs in: one built console
// serves every install, so nothing install-specific can be built into it. Each part is null when
// off.
type ClientConfig struct {
	// Sentry is where the console reports its errors (decision 0039).
	Sentry *SentryClient `json:"sentry"`
}

// SentryClient is the console's Sentry project. A browser DSN is public by design: it lets the page
// send events and nothing else.
type SentryClient struct {
	DSN              string  `json:"dsn"`
	Environment      string  `json:"environment"`
	TracesSampleRate float64 `json:"tracesSampleRate"`
}

// clientConfig serves GET /auth/client-config to anyone, as /auth/providers is.
func (s *Service) clientConfig(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Cache-Control", "public, max-age=60")
	writeJSON(w, http.StatusOK, s.Client)
}
