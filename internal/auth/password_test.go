package auth

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"testing"

	"golang.org/x/crypto/argon2"
)

// phcWith builds a PHC string with the given parameters and a real argon2id key, as another
// version of HashPassword might have stored it.
func phcWith(t *testing.T, password string, memory, time uint32, threads uint8, saltLen, keyLen int) string {
	t.Helper()
	salt := []byte(strings.Repeat("s", saltLen))
	key := argon2.IDKey([]byte(password), salt, time, memory, threads, uint32(keyLen)) //nolint:gosec // test sizes
	b64 := base64.RawStdEncoding
	return fmt.Sprintf("$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s", argon2.Version, memory, time, threads, b64.EncodeToString(salt), b64.EncodeToString(key))
}

// A stored hash's parameters are bounded: a bad or hostile row is refused before any hashing, never
// honoured at whatever cost it names.
func TestVerifyPasswordBoundsStoredParameters(t *testing.T) {
	b64 := base64.RawStdEncoding
	salt, key := b64.EncodeToString([]byte("0123456789abcdef")), b64.EncodeToString([]byte("0123456789abcdef0123456789abcdef"))
	for name, stored := range map[string]string{
		"4 GiB of memory":  fmt.Sprintf("$argon2id$v=19$m=%d,t=3,p=4$%s$%s", 4*1024*1024, salt, key),
		"no passes":        fmt.Sprintf("$argon2id$v=19$m=65536,t=0,p=4$%s$%s", salt, key),
		"100 passes":       fmt.Sprintf("$argon2id$v=19$m=65536,t=100,p=4$%s$%s", salt, key),
		"no threads":       fmt.Sprintf("$argon2id$v=19$m=65536,t=3,p=0$%s$%s", salt, key),
		"64 threads":       fmt.Sprintf("$argon2id$v=19$m=65536,t=3,p=64$%s$%s", salt, key),
		"a 4-byte salt":    fmt.Sprintf("$argon2id$v=19$m=65536,t=3,p=4$%s$%s", b64.EncodeToString([]byte("salt")), key),
		"an 8-byte key":    fmt.Sprintf("$argon2id$v=19$m=65536,t=3,p=4$%s$%s", salt, b64.EncodeToString([]byte("shortkey"))),
		"another version":  fmt.Sprintf("$argon2id$v=16$m=65536,t=3,p=4$%s$%s", salt, key),
		"argon2i":          fmt.Sprintf("$argon2i$v=19$m=65536,t=3,p=4$%s$%s", salt, key),
		"not a PHC string": "plaintext",
	} {
		ok, err := VerifyPassword(context.Background(), "anything", stored)
		if ok || !errors.Is(err, errBadHash) {
			t.Errorf("%s: ok %v, err %v; want errBadHash", name, ok, err)
		}
	}
}

// A hash made with other parameters still verifies, and NeedsRehash says to store it again; one made
// now does not need it.
func TestNeedsRehash(t *testing.T) {
	const pw = "correct horse battery staple"
	current, err := HashPassword(context.Background(), pw)
	if err != nil {
		t.Fatal(err)
	}
	for name, tt := range map[string]struct {
		stored string
		rehash bool
	}{
		"made now":           {current, false},
		"fewer passes":       {phcWith(t, pw, argonMemory, 2, argonThreads, saltLen, keyLen), true},
		"less memory":        {phcWith(t, pw, 19*1024, argonTime, argonThreads, saltLen, keyLen), true},
		"one thread":         {phcWith(t, pw, argonMemory, argonTime, 1, saltLen, keyLen), true},
		"a longer key":       {phcWith(t, pw, argonMemory, argonTime, argonThreads, saltLen, 64), true},
		"out of bounds (rd)": {"$argon2id$v=19$m=1,t=3,p=4$AAAA$AAAA", true},
	} {
		if got := NeedsRehash(tt.stored); got != tt.rehash {
			t.Errorf("%s: NeedsRehash = %v, want %v", name, got, tt.rehash)
		}
		if name == "out of bounds (rd)" {
			continue
		}
		if ok, err := VerifyPassword(context.Background(), pw, tt.stored); err != nil || !ok {
			t.Errorf("%s: VerifyPassword = %v, %v; want true", name, ok, err)
		}
	}
}

// The concurrency cap shares GOMAXPROCS among each hash's threads, and is never zero.
func TestHashingCap(t *testing.T) {
	if c := cap(hashing); c < 1 {
		t.Errorf("hashing cap = %d, want at least 1", c)
	}
}

// BenchmarkHashPassword is the cost of one sign-in's hash at t=3, m=64 MiB, p=4.
func BenchmarkHashPassword(b *testing.B) {
	for b.Loop() {
		if _, err := HashPassword(context.Background(), "correct horse battery staple"); err != nil {
			b.Fatal(err)
		}
	}
}
