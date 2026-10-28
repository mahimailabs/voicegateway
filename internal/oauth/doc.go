// Package oauth signs people in with Google and GitHub (phase 06h, decision 0035) and an Auth0
// tenant (decision 0036): the authorization code flow with PKCE through golang.org/x/oauth2, and
// Google's and Auth0's ID tokens verified by github.com/coreos/go-oidc/v3. It answers who a provider says signed in, a stable subject and one
// verified email; deciding which account that is, if any, is sign-in's (internal/auth).
package oauth
