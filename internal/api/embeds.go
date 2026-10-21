package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"slices"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"golang.org/x/net/idna"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// EmbedOrigins stores a project's allowed embed origins (decision 0034).
type EmbedOrigins interface {
	UpdateAllowedOrigins(ctx context.Context, org, project string, revision int64, origins []string) (store.ProjectSettings, error)
}

// EmbedMinter signs embed tokens (auth.EmbedTokens).
type EmbedMinter interface {
	Mint(grant store.EmbedScope, ttl time.Duration) (token string, expiresAt time.Time, err error)
}

// Embed token lifetimes: one hour unless the caller asks, never under a minute or over a day.
const (
	embedTTLDefault = 3600
	embedTTLMin     = 60
	embedTTLMax     = 86400
	maxEmbedBody    = 4 << 10
	maxOrigins      = 20
)

// embedWidgets are the widget ids a token may name: every widget some route admits.
func embedWidgets() []string {
	var out []string
	for _, ws := range embedAccess {
		for _, w := range ws {
			if !slices.Contains(out, w) {
				out = append(out, w)
			}
		}
	}
	slices.Sort(out)
	return out
}

var errOrigin = errors.New("an origin is https://host[:port], or http://localhost[:port] for development, with no path")

// NormaliseOrigin returns an origin as a browser serialises it (RFC 6454): lowercase scheme and
// host, and the port only when it is not the scheme's default. It accepts https, and http only for
// localhost and loopback addresses, and refuses a path, query, fragment, user info and wildcards, so
// an entry names exactly one site and compares equal to what frame-ancestors matches.
func NormaliseOrigin(s string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(s))
	if err != nil || u.Opaque != "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return "", errOrigin
	}
	scheme, host, port := strings.ToLower(u.Scheme), strings.ToLower(u.Hostname()), u.Port()
	// A browser serialises an internationalised host in its ASCII (punycode) form.
	if _, ipErr := netip.ParseAddr(host); ipErr != nil && host != "" {
		if host, err = idna.Lookup.ToASCII(host); err != nil {
			return "", errOrigin
		}
	}
	if host == "" || len(host) > 253 || strings.ContainsAny(host, "*%") || strings.HasSuffix(host, ".") {
		return "", errOrigin
	}
	switch {
	case scheme == "https" && port == "443", scheme == "http" && port == "80":
		port = ""
	}
	if scheme != "https" && (scheme != "http" || !loopback(host)) {
		return "", errOrigin
	}
	if port != "" {
		return scheme + "://" + net.JoinHostPort(host, port), nil
	}
	if strings.Contains(host, ":") {
		return scheme + "://[" + host + "]", nil
	}
	return scheme + "://" + host, nil
}

func loopback(host string) bool {
	if host == "localhost" {
		return true
	}
	a, err := netip.ParseAddr(host)
	return err == nil && a.IsLoopback()
}

// normaliseOrigins normalises an allow-list, keeping the first of any duplicates; at most 20.
func normaliseOrigins(in []string) ([]string, error) {
	if len(in) > maxOrigins {
		return nil, fmt.Errorf("at most %d allowed origins", maxOrigins)
	}
	out := make([]string, 0, len(in))
	for _, s := range in {
		o, err := NormaliseOrigin(s)
		if err != nil {
			return nil, err
		}
		if !slices.Contains(out, o) {
			out = append(out, o)
		}
	}
	return out, nil
}

// decodeOne decodes exactly one JSON object of at most limit bytes with no unknown fields.
func decodeOne(w http.ResponseWriter, r *http.Request, limit int64, v any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, limit))
	dec.DisallowUnknownFields()
	var trailing any
	return dec.Decode(v) == nil && errors.Is(dec.Decode(&trailing), io.EOF)
}

// updateAllowedOrigins serves operationId updateAllowedOrigins: replaces the sites allowed to frame
// the project's widgets. An empty list or null clears it, which allows none and stops every embed
// token at its next request.
func (a *API) updateAllowedOrigins(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Origins == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "project settings unavailable")
		return
	}
	var body struct {
		AllowedOrigins *[]string `json:"allowedOrigins"`
		Revision       *int64    `json:"revision"`
	}
	if !decodeOne(w, r, maxEmbedBody, &body) || body.Revision == nil || *body.Revision < 0 || *body.Revision >= 9007199254740991 {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected one JSON object with allowedOrigins and revision, at most 4 KiB")
		return
	}
	var origins []string
	if body.AllowedOrigins != nil {
		var err error
		if origins, err = normaliseOrigins(*body.AllowedOrigins); err != nil {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
			return
		}
	}
	settings, err := a.Origins.UpdateAllowedOrigins(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), *body.Revision, origins)
	if errors.Is(err, store.ErrSettingsConflict) {
		writeError(w, r, http.StatusConflict, "revision_conflict", "settings changed; reload before saving")
		return
	}
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	writeJSON(w, http.StatusOK, a.settingsResponse(settings))
}

type embedToken struct {
	Token     string    `json:"token"`
	ExpiresAt time.Time `json:"expiresAt"`
	Widgets   []string  `json:"widgets"`
	Origin    string    `json:"origin"`
}

// createEmbedToken serves operationId createEmbedToken: a token for some of the project's widgets,
// framed by one of its allowed origins, for 1 minute to 24 hours (1 hour by default). A platform's
// backend calls it with a project key and hands the token to its page; the key never reaches the
// browser. Nothing is stored: the token is signed, and checked again on every request.
func (a *API) createEmbedToken(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Embeds == nil || a.Settings == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "embed tokens unavailable")
		return
	}
	var body struct {
		Widgets          []string `json:"widgets"`
		Origin           string   `json:"origin"`
		ExpiresInSeconds *int     `json:"expiresInSeconds"`
	}
	known := embedWidgets()
	ttl := embedTTLDefault
	if !decodeOne(w, r, maxEmbedBody, &body) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected one JSON object with widgets, origin and optionally expiresInSeconds, at most 4 KiB")
		return
	}
	if body.ExpiresInSeconds != nil {
		ttl = *body.ExpiresInSeconds
	}
	widgets := make([]string, 0, len(body.Widgets))
	for _, wd := range body.Widgets {
		if !slices.Contains(known, wd) {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "widgets must be some of "+strings.Join(known, ", "))
			return
		}
		if !slices.Contains(widgets, wd) {
			widgets = append(widgets, wd)
		}
	}
	origin, originErr := NormaliseOrigin(body.Origin)
	switch {
	case len(widgets) == 0:
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "name at least one widget: "+strings.Join(known, ", "))
		return
	case originErr != nil:
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, originErr.Error())
		return
	case ttl < embedTTLMin || ttl > embedTTLMax:
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expiresInSeconds must be between 60 and 86400")
		return
	}
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	settings, err := a.Settings.ProjectSettings(r.Context(), org, proj)
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	// Missing configuration allows no origin.
	if !slices.Contains(settings.AllowedOrigins, origin) {
		writeError(w, r, http.StatusBadRequest, "origin_not_allowed", "this origin is not in the project's allowed origins; an Admin adds it in Settings, Embeds")
		return
	}
	p, _ := store.PrincipalFrom(r.Context())
	grant := store.EmbedScope{OrganisationID: org, ProjectID: proj, Widgets: widgets, Origin: origin, UserID: p.UserID}
	if p.Key != nil {
		grant.KeyID, grant.UserID = p.Key.KeyID, ""
	}
	if grant.KeyID == "" && grant.UserID == "" {
		writeError(w, r, http.StatusForbidden, codeForbidden, "a project key or a member mints embed tokens")
		return
	}
	token, expiresAt, err := a.Embeds.Mint(grant, time.Duration(ttl)*time.Second)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	writeJSON(w, http.StatusCreated, embedToken{Token: token, ExpiresAt: expiresAt.UTC(), Widgets: widgets, Origin: origin})
}
