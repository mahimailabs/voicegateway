package auth

import (
	"context"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// An embed token (decision 0034) is vge_ followed by a JWT that VoiceGateway signs with HS256 under
// a key derived from VOICEGATEWAY_SECRET_KEY. It names one project, the widgets it opens, the one
// origin allowed to frame them, who minted it and when it expires. It is checked again on every
// request: the minting key or member must still be active and the origin still allowed, so revoking
// either stops a token before it expires.
const (
	embedPrefix   = "vge_"
	embedIssuer   = "voicegateway"
	embedAudience = "voicegateway-embed"
	// embedKeyInfo separates the token key from every other use of VOICEGATEWAY_SECRET_KEY.
	embedKeyInfo = "voicegateway embed token v1"
	// maxEmbedToken bounds what the gate parses; a real token is a few hundred bytes.
	maxEmbedToken = 4 << 10
)

// Embed token lifetimes: one hour unless the caller asks, never under a minute or over a day.
const (
	EmbedTTLDefault = time.Hour
	EmbedTTLMin     = time.Minute
	EmbedTTLMax     = 24 * time.Hour
)

// ErrEmbedToken is any token that is not a valid, unexpired embed token signed by this server.
var ErrEmbedToken = errors.New("not a valid embed token")

// EmbedTokens mints and checks embed tokens with one derived key.
type EmbedTokens struct {
	key []byte
	Now func() time.Time
}

// NewEmbedTokens derives the token key from the 32-byte VOICEGATEWAY_SECRET_KEY with HKDF-SHA256.
func NewEmbedTokens(secretKey []byte) (*EmbedTokens, error) {
	if len(secretKey) < 32 {
		return nil, errors.New("embed tokens need the 32-byte VOICEGATEWAY_SECRET_KEY")
	}
	key, err := hkdf.Key(sha256.New, secretKey, nil, embedKeyInfo, 32)
	if err != nil {
		return nil, fmt.Errorf("embed token key: %w", err)
	}
	return &EmbedTokens{key: key}, nil
}

func (t *EmbedTokens) now() time.Time {
	if t.Now != nil {
		return t.Now()
	}
	return time.Now()
}

type embedClaims struct {
	Organisation string   `json:"org"`
	Project      string   `json:"prj"`
	Widgets      []string `json:"wgt"`
	Origin       string   `json:"ori"`
	// Minter is "key:<key_id>" or "user:<user id>".
	Minter string `json:"by"`
	// Content is the opt-in to transcripts and recording audio.
	Content bool `json:"cnt,omitempty"`
	jwt.RegisteredClaims
}

// Mint signs a token for grant, valid from now for ttl. It does not check the grant: the caller
// checks the widgets, the origin and its own access first.
func (t *EmbedTokens) Mint(grant store.EmbedScope, ttl time.Duration) (token string, expiresAt time.Time, err error) {
	var jti [16]byte
	if _, err := rand.Read(jti[:]); err != nil {
		return "", time.Time{}, fmt.Errorf("embed token id: %w", err)
	}
	now := t.now().Truncate(time.Second)
	expiresAt = now.Add(ttl)
	minter := "user:" + grant.UserID
	if grant.KeyID != "" {
		minter = "key:" + grant.KeyID
	}
	claims := embedClaims{
		Organisation: grant.OrganisationID, Project: grant.ProjectID, Widgets: grant.Widgets, Origin: grant.Origin, Minter: minter, Content: grant.Content,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer: embedIssuer, Audience: jwt.ClaimStrings{embedAudience}, Subject: grant.OrganisationID + "/" + grant.ProjectID,
			IssuedAt: jwt.NewNumericDate(now), NotBefore: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(expiresAt),
			ID: base64.RawURLEncoding.EncodeToString(jti[:]),
		},
	}
	signed, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(t.key)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("sign embed token: %w", err)
	}
	return embedPrefix + signed, expiresAt, nil
}

// Parse checks a token's signature, issuer, audience and lifetime, HS256 only, and returns its
// grant. Anything else is ErrEmbedToken. It does not check that the minter is still active or the
// origin still allowed: that needs the store (Service.embedPrincipal).
func (t *EmbedTokens) Parse(v string) (store.EmbedScope, error) {
	raw, ok := strings.CutPrefix(v, embedPrefix)
	if !ok || len(v) > maxEmbedToken {
		return store.EmbedScope{}, ErrEmbedToken
	}
	var c embedClaims
	_, err := jwt.ParseWithClaims(raw, &c, func(*jwt.Token) (any, error) { return t.key, nil },
		jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}),
		jwt.WithIssuer(embedIssuer), jwt.WithAudience(embedAudience),
		jwt.WithExpirationRequired(), jwt.WithIssuedAt(), jwt.WithTimeFunc(t.now),
	)
	if err != nil {
		return store.EmbedScope{}, ErrEmbedToken
	}
	g := store.EmbedScope{OrganisationID: c.Organisation, ProjectID: c.Project, Widgets: c.Widgets, Origin: c.Origin, Content: c.Content}
	switch kind, id, _ := strings.Cut(c.Minter, ":"); kind {
	case "key":
		g.KeyID = id
	case "user":
		g.UserID = id
	}
	// A token this server signed always has these; one without them is not trusted with anything.
	if g.OrganisationID == "" || g.ProjectID == "" || g.Origin == "" || len(g.Widgets) == 0 || (g.KeyID == "") == (g.UserID == "") ||
		c.ExpiresAt.Sub(c.IssuedAt.Time) > EmbedTTLMax {
		return store.EmbedScope{}, ErrEmbedToken
	}
	return g, nil
}

// embedPrincipal checks a Bearer value as an embed token: signed here, unexpired, and its grant
// still active in the store, in one round trip. Any refusal reads as no credential.
func (s *Service) embedPrincipal(ctx context.Context, v string) (store.Principal, bool, error) {
	g, ok, err := s.EmbedGrant(ctx, v)
	if err != nil || !ok {
		return store.Principal{}, false, err
	}
	return store.Principal{Embed: &g}, true, nil
}

// EmbedGrant returns the grant of a valid embed token whose minter is still active and whose origin
// the project still allows. A token that fails any check is false with no error; only a store
// failure is an error. With no EmbedTokens set, every token is refused.
func (s *Service) EmbedGrant(ctx context.Context, v string) (store.EmbedScope, bool, error) {
	if s.Embeds == nil {
		return store.EmbedScope{}, false, nil
	}
	g, err := s.Embeds.Parse(v)
	if err != nil {
		return store.EmbedScope{}, false, nil
	}
	active, err := s.Users.EmbedGrantActive(ctx, g)
	if err != nil {
		return store.EmbedScope{}, false, err
	}
	return g, active, nil
}

// IsEmbedToken reports whether a Bearer value is shaped as an embed token, so the gate never tries
// it as a project key.
func IsEmbedToken(v string) bool { return strings.HasPrefix(v, embedPrefix) }

// embedPath is a widget's console address, /org/<org>/project/<project>/embed/<widget>.
var embedPath = regexp.MustCompile(`^/org/([^/]+)/project/([^/]+)/embed/([^/]+)/?$`)

// Frames sets who may frame each console page. A widget page whose ?token= is a valid embed token
// for that project and widget may be framed by the token's origin only; any other page, and a widget
// page without such a token, by the console itself only (the Settings preview). Widget pages send no
// Referer, since their address holds the token.
func (s *Service) Frames(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ancestors := "'self'"
		if m := embedPath.FindStringSubmatch(r.URL.Path); m != nil {
			w.Header().Set("Referrer-Policy", "no-referrer")
			if v := r.URL.Query().Get("token"); v != "" {
				g, ok, err := s.EmbedGrant(r.Context(), v)
				if err != nil {
					s.fail(w, err)
					return
				}
				if ok && g.OrganisationID == m[1] && g.ProjectID == m[2] && slices.Contains(g.Widgets, m[3]) {
					ancestors = g.Origin
				}
			}
		}
		w.Header().Set("Content-Security-Policy", "frame-ancestors "+ancestors)
		next.ServeHTTP(w, r)
	})
}
