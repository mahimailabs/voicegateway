package api

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5/middleware"
)

// Error codes of the contract's Error schema.
const (
	codeInvalidRequest  = "invalid_request"
	codeUnauthenticated = "unauthenticated"
	codeNotFound        = "not_found"
	codeInternal        = "internal_error"
)

// apiError is the contract's Error: a stable code, a safe message and the request id. It never
// carries an upstream credential, a request body or a storage error's text.
type apiError struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	RequestID string `json:"requestId"`
}

func writeError(w http.ResponseWriter, r *http.Request, status int, code, message string) {
	writeJSON(w, status, apiError{Code: code, Message: message, RequestID: middleware.GetReqID(r.Context())})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	body, err := json.Marshal(v)
	if err != nil {
		http.Error(w, `{"code":"internal_error","message":"response could not be encoded","requestId":""}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
