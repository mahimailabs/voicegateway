package auth

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// An embed token opens only when this server signed it, with HS256, for the embed audience, and it
// has not expired; anything else reads as no token.
func TestEmbedTokenParse(t *testing.T) {
	now := time.Date(2026, 10, 21, 3, 0, 0, 0, time.UTC)
	clock := func() time.Time { return now }
	tokens, err := NewEmbedTokens(bytes.Repeat([]byte{7}, 32))
	if err != nil {
		t.Fatal(err)
	}
	tokens.Now = clock
	other, _ := NewEmbedTokens(bytes.Repeat([]byte{8}, 32))
	other.Now = clock
	grant := store.EmbedScope{OrganisationID: "default", ProjectID: "one", Widgets: []string{"recent-calls", "call"}, Origin: "https://app.example.com", KeyID: "abcdefghijkl"}
	good, expires, err := tokens.Mint(grant, time.Hour)
	if err != nil || !expires.Equal(now.Add(time.Hour)) || !strings.HasPrefix(good, "vge_") {
		t.Fatalf("mint: %q %v %v", good, expires, err)
	}
	foreign, _, _ := other.Mint(grant, time.Hour)

	// A token signed with the same key but another algorithm, audience or lifetime.
	sign := func(method jwt.SigningMethod, key any, mutate func(*embedClaims)) string {
		c := embedClaims{
			Organisation: "default", Project: "one", Widgets: []string{"calls"}, Origin: "https://app.example.com", Minter: "key:abcdefghijkl",
			RegisteredClaims: jwt.RegisteredClaims{
				Issuer: embedIssuer, Audience: jwt.ClaimStrings{embedAudience},
				IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(now.Add(time.Hour)),
			},
		}
		if mutate != nil {
			mutate(&c)
		}
		s, err := jwt.NewWithClaims(method, c).SignedString(key)
		if err != nil {
			t.Fatal(err)
		}
		return embedPrefix + s
	}
	none := sign(jwt.SigningMethodNone, jwt.UnsafeAllowNoneSignatureType, nil)

	got, err := tokens.Parse(good)
	if err != nil || got.OrganisationID != "default" || got.ProjectID != "one" || got.Origin != grant.Origin || got.KeyID != grant.KeyID || got.UserID != "" || strings.Join(got.Widgets, ",") != "recent-calls,call" {
		t.Fatalf("parse a good token: %+v %v", got, err)
	}
	if _, err := tokens.Parse(sign(jwt.SigningMethodHS256, tokens.key, nil)); err != nil {
		t.Fatalf("a token signed the same way: %v", err)
	}
	for name, v := range map[string]string{
		"another server's key": foreign,
		"no prefix":            strings.TrimPrefix(good, "vge_"),
		"tampered":             good[:len(good)-2] + "AA",
		"alg none":             none,
		"HS512":                sign(jwt.SigningMethodHS512, tokens.key, nil),
		"wrong audience":       sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.Audience = jwt.ClaimStrings{"voicegateway-api"} }),
		"wrong issuer":         sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.Issuer = "someone" }),
		"no expiry":            sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.ExpiresAt = nil }),
		"expired":              sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.ExpiresAt = jwt.NewNumericDate(now.Add(-time.Second)) }),
		"issued in the future": sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.IssuedAt = jwt.NewNumericDate(now.Add(time.Minute)) }),
		"over a day":           sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.ExpiresAt = jwt.NewNumericDate(now.Add(25 * time.Hour)) }),
		"no origin":            sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.Origin = "" }),
		"no widgets":           sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.Widgets = nil }),
		"no minter":            sign(jwt.SigningMethodHS256, tokens.key, func(c *embedClaims) { c.Minter = "" }),
		"too long":             good + strings.Repeat("A", maxEmbedToken),
	} {
		if g, err := tokens.Parse(v); err == nil {
			t.Errorf("%s: parsed as %+v", name, g)
		}
	}
	// The same token after it expires.
	now = now.Add(time.Hour)
	if _, err := tokens.Parse(good); err == nil {
		t.Error("a token parsed at its expiry")
	}
}
