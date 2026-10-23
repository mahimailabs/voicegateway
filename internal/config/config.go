package config

import (
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"strconv"
	"strings"
)

// Config is every setting the web and worker roles read at start.
type Config struct {
	// CloudQuotas requires operator-issued entitlements before accepting telemetry.
	CloudQuotas bool
	// WebAddr is where the web role listens.
	WebAddr string
	// PostgresURL holds projects, keys and settings.
	PostgresURL string
	// ClickHouseURL holds everything measured.
	ClickHouseURL string
	// RedisURL carries the ingest stream between web and worker.
	RedisURL string
	// S3 is the bucket recordings are stored in.
	S3 S3
	// SecretKey seals the secrets VoiceGateway must read back, such as each project's LiveKit API
	// secret (decision 0016). 32 bytes, given base64-encoded.
	SecretKey []byte
	// LogLevel is debug, info, warn or error.
	LogLevel slog.Level
	// PublicURL is the address people open the console at; https makes every session cookie
	// Secure. Empty means unknown, and each request's own transport decides.
	PublicURL string
	// TrustedProxies are the proxies (addresses and CIDRs, comma-separated) whose X-Forwarded-For
	// and X-Forwarded-Proto web believes.
	TrustedProxies string
	// SMTPURL is the SMTP server invitations are emailed through (smtps:// or smtp:// with
	// STARTTLS); empty sends nothing, and the console shows links to copy. It holds a password and is
	// never logged. SMTPFrom is the sender's address, required with it.
	SMTPURL, SMTPFrom string
	// WorkerEndpointsPublicOnly makes the worker refuse private and loopback addresses for worker
	// metrics endpoints, as well as the link-local and metadata addresses it always refuses. It also
	// puts every LiveKit API read the worker makes behind that same guard, so a project's LiveKit URL
	// cannot reach the install's network either (scrape.NewDial). For an install whose endpoints and
	// projects are entered by people it should not trust with its network, such as Cloud; a
	// self-hosted install's workers usually sit on its own private network.
	WorkerEndpointsPublicOnly bool
	// InstanceTokenSHA256 is the SHA-256 of VOICEGATEWAY_INSTANCE_TOKEN, which turns the instance
	// API on (phase 06e); nil when unset. The token itself is not kept. Setting it requires
	// WorkerEndpointsPublicOnly.
	InstanceTokenSHA256 []byte
	// Google and GitHub are the OAuth apps of Google and GitHub sign-in (phase 06h, decision 0035);
	// zero is off. Either needs PublicURL, the base of its callback.
	Google, GitHub OAuthApp
	// Auth0 is an Auth0 tenant's application (decision 0036); zero is off. Like Google and GitHub
	// it needs PublicURL.
	Auth0 Auth0App
	// ConsoleDir is the built dashboard web serves (decision 0011); the image sets it. Empty serves
	// no console, as in development, where the Vite dev server runs it.
	ConsoleDir string
}

// OAuthApp is one provider's OAuth app: both set, or neither. The secret is never logged.
type OAuthApp struct {
	ClientID, ClientSecret string
}

// On reports whether the app is configured.
func (a OAuthApp) On() bool { return a.ClientID != "" }

// Auth0App is an Auth0 application: its tenant's domain with its OAuth app, all three set or
// none.
type Auth0App struct {
	// Domain is the tenant or custom domain, a bare host such as voicegateway.us.auth0.com.
	Domain string
	OAuthApp
}

// Callback is the provider's callback URL for a public URL: the one to register with it exactly.
func Callback(publicURL, provider string) string {
	return strings.TrimRight(publicURL, "/") + "/auth/oauth/" + provider + "/callback"
}

// S3 is any S3-compatible bucket.
type S3 struct {
	Endpoint  string
	Bucket    string
	AccessKey string
	SecretKey string
	UseSSL    bool
}

// Load reads the configuration through getenv, normally os.Getenv. It names every missing or
// invalid variable at once.
func Load(getenv func(string) string) (Config, error) {
	var errs []error
	required := func(name string) string {
		v := strings.TrimSpace(getenv(name))
		if v == "" {
			errs = append(errs, fmt.Errorf("%s is required", name))
		}
		return v
	}
	optional := func(name, fallback string) string {
		if v := strings.TrimSpace(getenv(name)); v != "" {
			return v
		}
		return fallback
	}

	c := Config{
		WebAddr:        optional("VOICEGATEWAY_WEB_ADDR", ":8080"),
		PublicURL:      optional("VOICEGATEWAY_PUBLIC_URL", ""),
		TrustedProxies: optional("VOICEGATEWAY_TRUSTED_PROXIES", ""),
		SMTPURL:        optional("VOICEGATEWAY_SMTP_URL", ""),
		SMTPFrom:       optional("VOICEGATEWAY_SMTP_FROM", ""),
		ConsoleDir:     optional("VOICEGATEWAY_CONSOLE_DIR", ""),
		PostgresURL:    required("VOICEGATEWAY_POSTGRES_URL"),
		ClickHouseURL:  required("VOICEGATEWAY_CLICKHOUSE_URL"),
		RedisURL:       required("VOICEGATEWAY_REDIS_URL"),
		S3: S3{
			Endpoint:  required("VOICEGATEWAY_S3_ENDPOINT"),
			Bucket:    required("VOICEGATEWAY_S3_BUCKET"),
			AccessKey: required("VOICEGATEWAY_S3_ACCESS_KEY"),
			SecretKey: required("VOICEGATEWAY_S3_SECRET_KEY"),
		},
	}

	if raw := required("VOICEGATEWAY_SECRET_KEY"); raw != "" {
		key, err := base64.StdEncoding.DecodeString(raw)
		if err != nil || len(key) != 32 {
			errs = append(errs, errors.New("VOICEGATEWAY_SECRET_KEY must be 32 bytes, base64-encoded (openssl rand -base64 32)"))
		}
		c.SecretKey = key
	}

	ssl, err := strconv.ParseBool(optional("VOICEGATEWAY_S3_USE_SSL", "false"))
	if err != nil {
		errs = append(errs, errors.New("VOICEGATEWAY_S3_USE_SSL must be true or false"))
	}
	c.S3.UseSSL = ssl
	publicOnly, publicOnlyErr := strconv.ParseBool(optional("VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY", "false"))
	if publicOnlyErr != nil {
		errs = append(errs, errors.New("VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY must be true or false"))
	}
	c.WorkerEndpointsPublicOnly = publicOnly

	// The token is a credential: an error names the variable, never its value.
	if token := optional("VOICEGATEWAY_INSTANCE_TOKEN", ""); token != "" {
		if len(token) < 32 {
			errs = append(errs, errors.New("VOICEGATEWAY_INSTANCE_TOKEN must be at least 32 characters (openssl rand -base64 32)"))
		}
		sum := sha256.Sum256([]byte(token))
		c.InstanceTokenSHA256 = sum[:]
		// The instance token marks a shared instance (decision 0028), whose organisations add projects
		// and endpoints that must not reach its network. Every role refuses to start without the
		// guard, so a worker can never read a hosted project unguarded and web never accepts one for
		// it. An unparseable flag is already named above.
		if publicOnlyErr == nil && !publicOnly {
			errs = append(errs, errors.New("VOICEGATEWAY_INSTANCE_TOKEN is set, so VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY must be true: a shared instance reads its organisations' LiveKit projects and worker endpoints only through the address guard"))
		}
	}

	// Each provider's id and secret come as a pair, and its callback is built from the public URL,
	// which must be https, or http on localhost, for a provider to accept it.
	for _, p := range []struct {
		name string
		app  *OAuthApp
	}{{"GOOGLE", &c.Google}, {"GITHUB", &c.GitHub}, {"AUTH0", &c.Auth0.OAuthApp}} {
		id, secret := "VOICEGATEWAY_"+p.name+"_CLIENT_ID", "VOICEGATEWAY_"+p.name+"_CLIENT_SECRET"
		p.app.ClientID, p.app.ClientSecret = optional(id, ""), optional(secret, "")
		switch {
		case p.app.ClientID != "" && p.app.ClientSecret == "":
			errs = append(errs, fmt.Errorf("%s is set, so %s is required", id, secret))
		case p.app.ClientID == "" && p.app.ClientSecret != "":
			errs = append(errs, fmt.Errorf("%s is set, so %s is required", secret, id))
		case p.app.ClientID != "" && !callbackBase(c.PublicURL):
			errs = append(errs, fmt.Errorf("%s needs VOICEGATEWAY_PUBLIC_URL with https (or http://localhost), the base of its callback URL", id))
		}
	}

	// Auth0 also needs its tenant's domain, a bare host: the endpoints and issuer are built on it.
	c.Auth0.Domain = optional("VOICEGATEWAY_AUTH0_DOMAIN", "")
	switch {
	case c.Auth0.Domain == "" && c.Auth0.ClientID != "":
		errs = append(errs, errors.New("VOICEGATEWAY_AUTH0_CLIENT_ID is set, so VOICEGATEWAY_AUTH0_DOMAIN is required"))
	case c.Auth0.Domain != "" && c.Auth0.ClientID == "":
		errs = append(errs, errors.New("VOICEGATEWAY_AUTH0_DOMAIN is set, so VOICEGATEWAY_AUTH0_CLIENT_ID is required"))
	case c.Auth0.Domain != "" && !bareHost(c.Auth0.Domain):
		errs = append(errs, errors.New("VOICEGATEWAY_AUTH0_DOMAIN must be a bare host such as voicegateway.us.auth0.com, without https:// or a path"))
	}

	c.CloudQuotas, err = strconv.ParseBool(optional("VOICEGATEWAY_CLOUD_QUOTAS", "false"))
	if err != nil {
		errs = append(errs, errors.New("VOICEGATEWAY_CLOUD_QUOTAS must be true or false"))
	}
	if c.CloudQuotas && c.InstanceTokenSHA256 == nil {
		errs = append(errs, errors.New("VOICEGATEWAY_CLOUD_QUOTAS requires VOICEGATEWAY_INSTANCE_TOKEN"))
	}
	if err := c.LogLevel.UnmarshalText([]byte(optional("VOICEGATEWAY_LOG_LEVEL", "info"))); err != nil {
		errs = append(errs, errors.New("VOICEGATEWAY_LOG_LEVEL must be debug, info, warn or error"))
	}

	for name, raw := range map[string]string{
		"VOICEGATEWAY_POSTGRES_URL":   c.PostgresURL,
		"VOICEGATEWAY_CLICKHOUSE_URL": c.ClickHouseURL,
		"VOICEGATEWAY_REDIS_URL":      c.RedisURL,
	} {
		if raw == "" {
			continue
		}
		// The parse error can echo the URL, credentials included, so it is never wrapped.
		if u, err := url.Parse(raw); err != nil || u.Scheme == "" || u.Host == "" {
			errs = append(errs, fmt.Errorf("%s is not a valid URL", name))
		}
	}

	if len(errs) > 0 {
		return Config{}, errors.Join(errs...)
	}
	return c, nil
}

// callbackBase reports whether a public URL can be a provider callback's base: https, or http on
// localhost.
func callbackBase(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return false
	}
	return u.Scheme == "https" || u.Scheme == "http" && u.Hostname() == "localhost"
}

// bareHost reports whether raw is a host name alone, as an Auth0 domain is.
func bareHost(raw string) bool {
	u, err := url.Parse("https://" + raw)
	return err == nil && u.Host == raw && u.Port() == "" && u.User == nil && u.Path == "" && u.RawQuery == "" && u.Fragment == ""
}

// Redact returns a connection URL without its password, for logs and preflight output. Query
// parameters whose name says password, secret or token are masked too, since some drivers take
// credentials there (for example ?password= or ?x-amz-security-token=).
func Redact(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return "(unparseable URL)"
	}
	if u.RawQuery != "" {
		q := u.Query()
		for name := range q {
			if sensitiveParam(name) {
				q[name] = []string{"xxxxx"}
			}
		}
		u.RawQuery = q.Encode()
	}
	return u.Redacted()
}

func sensitiveParam(name string) bool {
	n := strings.ToLower(name)
	return strings.Contains(n, "password") || strings.Contains(n, "secret") || strings.Contains(n, "token")
}
