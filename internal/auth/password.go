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

// hashing caps concurrent hashes at the CPUs, so a burst of sign-ins queues instead of using
// 64 MiB per request without bound.
var hashing = make(chan struct{}, runtime.NumCPU())

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

// VerifyPassword reports whether password matches a PHC string HashPassword made, comparing in
// constant time.
func VerifyPassword(ctx context.Context, password, phc string) (bool, error) {
	parts := strings.Split(phc, "$")
	if len(parts) != 6 || parts[1] != "argon2id" {
		return false, errBadHash
	}
	var version int
	var memory, time uint32
	var threads uint8
	if _, err := fmt.Sscanf(parts[2], "v=%d", &version); err != nil || version != argon2.Version {
		return false, errBadHash
	}
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &memory, &time, &threads); err != nil {
		return false, errBadHash
	}
	b64 := base64.RawStdEncoding
	salt, err := b64.DecodeString(parts[4])
	if err != nil {
		return false, errBadHash
	}
	want, err := b64.DecodeString(parts[5])
	if err != nil || len(want) == 0 {
		return false, errBadHash
	}
	got, err := derive(ctx, password, salt, time, memory, threads, uint32(len(want))) //nolint:gosec // a decoded key is short
	if err != nil {
		return false, err
	}
	return subtle.ConstantTimeCompare(got, want) == 1, nil
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
