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
	}
	maps.Copy(base, overrides)
	return func(k string) string { return base[k] }
}

func TestConfigMissingAndRedacted(t *testing.T) {
	secrets := []string{"pg-secret", "ch-secret", "redis-secret", "s3-secret"}
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
	got := Redact("postgres://vg:pg-secret@postgres:5432/voicegateway")
	if strings.Contains(got, "pg-secret") || !strings.Contains(got, "postgres:5432") {
		t.Errorf("Redact() = %q, want host kept and password hidden", got)
	}
}
