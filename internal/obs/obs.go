package obs

import (
	"io"
	"log/slog"
	"net/http"
)

// Logger returns a JSON logger tagged with the role it runs in.
func Logger(w io.Writer, level slog.Level, role string) *slog.Logger {
	return slog.New(slog.NewJSONHandler(w, &slog.HandlerOptions{Level: level})).With("role", role)
}

// Health answers 200 while the process is serving. It says nothing about dependencies; preflight
// does that.
func Health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, `{"status":"ok"}`)
}
