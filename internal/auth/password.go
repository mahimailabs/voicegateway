package auth

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"runtime"
	"strings"
	"sync"

	"golang.org/x/crypto/argon2"
)

// argon2id parameters (spec 06a): t=3, m=64 MiB, p=4, a 16-byte salt and a 32-byte key.
const (
	argonTime    = 3
	argonMemory  = 64 * 1024
	argonThreads = 4
	saltLen      = 16
	keyLen       = 32
)

// hashing caps concurrent hashes, so a burst of sign-ins queues instead of using 64 MiB per request
// without bound. Each hash runs argonThreads threads, so the cap is GOMAXPROCS shared among them.
// GOMAXPROCS, not NumCPU: since Go 1.25 it follows a container's CPU limit, where NumCPU reports
// the host's cores.
var hashing = make(chan struct{}, max(1, runtime.GOMAXPROCS(0)/argonThreads))

// Bounds on the parameters VerifyPassword reads from a stored hash, so a bad or hostile row cannot
// make one sign-in take gigabytes or minutes. They are generous around the parameters above.
const (
	maxArgonTime    = 10
	maxArgonMemory  = 1024 * 1024 // KiB, 1 GiB
	maxArgonThreads = 16
	minSaltLen      = 8
	maxSaltLen      = 64
	minKeyLen       = 16
	maxKeyLen       = 64
)

// HashPassword returns the PHC string of an argon2id hash of password with a random salt.
func HashPassword(ctx context.Context, password string) (string, error) {
	salt := make([]byte, saltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", fmt.Errorf("salt: %w", err)
	}
	key, err := derive(ctx, password, salt, argonTime, argonMemory, argonThreads, keyLen)
	if err != nil {
		return "", err
	}
	b64 := base64.RawStdEncoding
	return fmt.Sprintf("$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s", argon2.Version, argonMemory, argonTime, argonThreads,
		b64.EncodeToString(salt), b64.EncodeToString(key)), nil
}

// errBadHash marks a stored hash that is not an argon2id PHC string.
var errBadHash = errors.New("stored password hash is not argon2id")

// phc is a parsed argon2id PHC string.
type phc struct {
	memory, time uint32
	threads      uint8
	salt, key    []byte
}

// parsePHC reads a PHC string HashPassword made, refusing parameters outside the bounds above.
func parsePHC(s string) (phc, error) {
	parts := strings.Split(s, "$")
	if len(parts) != 6 || parts[1] != "argon2id" {
		return phc{}, errBadHash
	}
	var version int
	if _, err := fmt.Sscanf(parts[2], "v=%d", &version); err != nil || version != argon2.Version {
		return phc{}, errBadHash
	}
	var h phc
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &h.memory, &h.time, &h.threads); err != nil {
		return phc{}, errBadHash
	}
	if h.time < 1 || h.time > maxArgonTime || h.memory < 8*uint32(h.threads) || h.memory > maxArgonMemory || h.threads < 1 || h.threads > maxArgonThreads {
		return phc{}, errBadHash
	}
	b64 := base64.RawStdEncoding
	var err error
	if h.salt, err = b64.DecodeString(parts[4]); err != nil || len(h.salt) < minSaltLen || len(h.salt) > maxSaltLen {
		return phc{}, errBadHash
	}
	if h.key, err = b64.DecodeString(parts[5]); err != nil || len(h.key) < minKeyLen || len(h.key) > maxKeyLen {
		return phc{}, errBadHash
	}
	return h, nil
}

// VerifyPassword reports whether password matches a PHC string HashPassword made, comparing in
// constant time. A stored hash with parameters outside the bounds is an error, never honoured.
func VerifyPassword(ctx context.Context, password, stored string) (bool, error) {
	h, err := parsePHC(stored)
	if err != nil {
		return false, err
	}
	got, err := derive(ctx, password, h.salt, h.time, h.memory, h.threads, uint32(len(h.key))) //nolint:gosec // bounded by maxKeyLen
	if err != nil {
		return false, err
	}
	return subtle.ConstantTimeCompare(got, h.key) == 1, nil
}

// NeedsRehash reports whether a stored hash was made with other parameters than HashPassword uses
// now, so a sign-in that proved the password can store it again with the current ones.
func NeedsRehash(stored string) bool {
	h, err := parsePHC(stored)
	if err != nil {
		return true
	}
	return h.memory != argonMemory || h.time != argonTime || h.threads != argonThreads || len(h.salt) != saltLen || len(h.key) != keyLen
}

func derive(ctx context.Context, password string, salt []byte, time, memory uint32, threads uint8, length uint32) ([]byte, error) {
	select {
	case hashing <- struct{}{}:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	defer func() { <-hashing }()
	return argon2.IDKey([]byte(password), salt, time, memory, threads, length), nil
}

// dummyHash is compared when the email is unknown, so an unknown email takes as long as a wrong
// password. It is made once, on first use.
var dummyHash = sync.OnceValues(func() (string, error) {
	return HashPassword(context.Background(), "not a password anyone has")
})
