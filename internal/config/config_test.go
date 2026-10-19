package config

import (
	"maps"
	"strings"
	"testing"
)

func env(overrides map[string]string) func(string) string {
	base := map[string]string{
		"VOICEGATEWAY_POSTGRES_URL":   "postgres://vg:pg-secret@postgres:5432/voicegateway",
		"VOICEGATEWAY_CLICKHOUSE_URL": "clickhouse://vg:ch-secret@clickhouse:9000/voicegateway",
		"VOICEGATEWAY_REDIS_URL":      "redis://:redis-secret@redis:6379/0",
		"VOICEGATEWAY_S3_ENDPOINT":    "s3:8333",
		"VOICEGATEWAY_S3_BUCKET":      "recordings",
		"VOICEGATEWAY_S3_ACCESS_KEY":  "vg",
		"VOICEGATEWAY_S3_SECRET_KEY":  "s3-secret",
		"VOICEGATEWAY_SECRET_KEY":     "c2VhbC1rZXktZm9yLXRlc3RzLW9ubHktMzItYnl0ZXM=",
	}
	maps.Copy(base, overrides)
	return func(k string) string { return base[k] }
}

func TestSelfHostedDoesNotEnableCloudQuotas(t *testing.T) {
	for _, value := range []string{"", "false"} {
		c, err := Load(env(map[string]string{"VOICEGATEWAY_CLOUD_QUOTAS": value}))
		if err != nil || c.CloudQuotas || c.InstanceTokenSHA256 != nil {
			t.Fatalf("self-hosted configuration requires Cloud access: quotas=%v err=%v", c.CloudQuotas, err)
		}
	}
	if _, err := Load(env(map[string]string{"VOICEGATEWAY_CLOUD_QUOTAS": "true"})); err == nil || !strings.Contains(err.Error(), "VOICEGATEWAY_INSTANCE_TOKEN") {
		t.Fatalf("Cloud enforcement must require explicit operator authentication: %v", err)
	}
}

func TestConfigMissingAndRedacted(t *testing.T) {
	secrets := []string{"pg-secret", "ch-secret", "redis-secret", "s3-secret", "c2hvcnQta2V5"}
	tests := []struct {
		name    string
		env     map[string]string
		wantErr []string
	}{
		{name: "complete", env: nil},
		{name: "one missing", env: map[string]string{"VOICEGATEWAY_REDIS_URL": ""}, wantErr: []string{"VOICEGATEWAY_REDIS_URL is required"}},
		{
			name:    "every missing is named",
			env:     map[string]string{"VOICEGATEWAY_POSTGRES_URL": " ", "VOICEGATEWAY_S3_BUCKET": ""},
			wantErr: []string{"VOICEGATEWAY_POSTGRES_URL is required", "VOICEGATEWAY_S3_BUCKET is required"},
		},
		{
			name:    "invalid URL is named without its secret",
			env:     map[string]string{"VOICEGATEWAY_CLICKHOUSE_URL": "ch-secret-without-scheme"},
			wantErr: []string{"VOICEGATEWAY_CLICKHOUSE_URL is not a valid URL"},
		},
		{
			name:    "secret key must be 32 bytes and is never echoed",
			env:     map[string]string{"VOICEGATEWAY_SECRET_KEY": "c2hvcnQta2V5"},
			wantErr: []string{"VOICEGATEWAY_SECRET_KEY must be 32 bytes"},
		},
		// Retired in phase 06c: a left-over token is no error, so an upgrade with an old .env starts.
		{name: "retired API token is ignored", env: map[string]string{"VOICEGATEWAY_API_TOKEN": "too-short"}},
		{name: "bad flag", env: map[string]string{"VOICEGATEWAY_S3_USE_SSL": "maybe"}, wantErr: []string{"VOICEGATEWAY_S3_USE_SSL must be true or false"}},
		{name: "bad level", env: map[string]string{"VOICEGATEWAY_LOG_LEVEL": "loud"}, wantErr: []string{"VOICEGATEWAY_LOG_LEVEL must be"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c, err := Load(env(tt.env))
			if len(tt.wantErr) == 0 {
				if err != nil {
					t.Fatalf("Load() error = %v", err)
				}
				if c.WebAddr != ":8080" {
					t.Errorf("WebAddr = %q, want the :8080 default", c.WebAddr)
				}
				return
			}
			if err == nil {
				t.Fatal("Load() error = nil, want an error")
			}
			for _, want := range tt.wantErr {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("error %q does not name %q", err, want)
				}
			}
			for _, s := range append(secrets, "ch-secret-without-scheme") {
				if strings.Contains(err.Error(), s) {
					t.Errorf("error leaks a secret: %q", err)
				}
			}
		})
	}
}

func TestRedact(t *testing.T) {
	tests := []struct {
		name, raw string
		hidden    []string
		kept      []string
	}{
		{name: "userinfo password", raw: "postgres://vg:pg-secret@postgres:5432/voicegateway", hidden: []string{"pg-secret"}, kept: []string{"postgres:5432", "vg"}},
		{
			name:   "credential query parameters",
			raw:    "clickhouse://clickhouse:9000/voicegateway?username=vg&password=ch-secret&client_secret=cs-secret&X-Amz-Security-Token=tok-secret&dial_timeout=200ms",
			hidden: []string{"ch-secret", "cs-secret", "tok-secret"},
			kept:   []string{"clickhouse:9000", "username=vg", "dial_timeout=200ms", "password=xxxxx"},
		},
		{name: "no query", raw: "redis://redis:6379/0", kept: []string{"redis://redis:6379/0"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := Redact(tt.raw)
			for _, s := range tt.hidden {
				if strings.Contains(got, s) {
					t.Errorf("Redact() = %q, leaks %q", got, s)
				}
			}
			for _, s := range tt.kept {
				if !strings.Contains(got, s) {
					t.Errorf("Redact() = %q, lost %q", got, s)
				}
			}
		})
	}
}

// A shared instance (the instance token is set) refuses to start without the worker address guard,
// in every role; a self-hosted instance starts with either value.
func TestSharedInstanceNeedsPublicOnly(t *testing.T) {
	const token = "aW5zdGFuY2UtdG9rZW4tZm9yLXRoZS10ZXN0cy0wMDA="
	tests := []struct {
		name       string
		token      string
		publicOnly string
		wantErr    bool
	}{
		{"shared, public-only unset", token, "", true},
		{"shared, public-only false", token, "false", true},
		{"shared, public-only true", token, "true", false},
		{"self-hosted, public-only unset", "", "", false},
		{"self-hosted, public-only false", "", "false", false},
		{"self-hosted, public-only true", "", "true", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c, err := Load(env(map[string]string{
				"VOICEGATEWAY_INSTANCE_TOKEN":               tt.token,
				"VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY": tt.publicOnly,
			}))
			if !tt.wantErr {
				if err != nil {
					t.Fatalf("Load() error = %v", err)
				}
				if want := tt.publicOnly == "true"; c.WorkerEndpointsPublicOnly != want {
					t.Errorf("WorkerEndpointsPublicOnly = %v, want %v", c.WorkerEndpointsPublicOnly, want)
				}
				return
			}
			if err == nil {
				t.Fatal("Load() started a shared instance without the address guard")
			}
			for _, name := range []string{"VOICEGATEWAY_INSTANCE_TOKEN", "VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY must be true"} {
				if !strings.Contains(err.Error(), name) {
					t.Errorf("error %q does not name %s", err, name)
				}
			}
			if strings.Contains(err.Error(), token) {
				t.Error("the error holds the instance token")
			}
		})
	}
	// An unparseable flag is named once, as itself, not again as the shared-instance rule.
	_, err := Load(env(map[string]string{"VOICEGATEWAY_INSTANCE_TOKEN": token, "VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY": "maybe"}))
	if err == nil || !strings.Contains(err.Error(), "VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY must be true or false") || strings.Contains(err.Error(), "is set, so") {
		t.Errorf("Load() with an unparseable flag = %v", err)
	}
}
