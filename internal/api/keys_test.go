package api

import "testing"

// The one key name rule, shared by createProjectKey and voicegateway key create.
func TestKeyName(t *testing.T) {
	long := ""
	for range 65 {
		long += "é"
	}
	for _, tt := range []struct {
		in, want string
		ok       bool
	}{
		{"ci", "ci", true},
		{"  ci  ", "ci", true},
		{long[:len(long)-len("é")], long[:len(long)-len("é")], true}, // 64 runes, 128 bytes
		{"", "", false},
		{"   ", "", false},
		{long, "", false},
		{"a\x00b", "", false},
		{"a\nb", "", false},
	} {
		got, err := KeyName(tt.in)
		if (err == nil) != tt.ok || got != tt.want {
			t.Errorf("KeyName(%q) = %q, %v; want %q, ok %v", tt.in, got, err, tt.want, tt.ok)
		}
	}
}
