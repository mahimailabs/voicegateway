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
	// AnswerBudgetMs is the answer budget listCalls counts against; 0 is the console's default.
	// Interim, until thresholds are stored per project.
	AnswerBudgetMs int
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
	// ConsoleDir is the built dashboard web serves (decision 0011); the image sets it. Empty serves
	// no console, as in development, where the Vite dev server runs it.
	ConsoleDir string
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

	if v := optional("VOICEGATEWAY_ANSWER_BUDGET_MS", ""); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 {
			errs = append(errs, errors.New("VOICEGATEWAY_ANSWER_BUDGET_MS must be a whole number of milliseconds, at least 1"))
		}
		c.AnswerBudgetMs = n
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
