package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base32"
	"encoding/base64"
	"fmt"
	"strings"
)

// A project API key (phase 06c, decision 0027) is vgk_<key_id>_<secret>: 4 + 12 + 1 + 43 = 60
// characters. key_id is lowercase base32 and finds the stored row; the secret is 32 random bytes
// in unpadded base64url, and only its SHA-256 is stored. The key is read by fixed lengths: "_" is
// in the base64url alphabet, so splitting on it would misread a secret that holds one.
const (
	keyPrefix    = "vgk_"
	keyIDLength  = 12
	secretBytes  = 32
	secretLength = 43
	keyLength    = len(keyPrefix) + keyIDLength + 1 + secretLength
)

var (
	keyIDEncoding  = base32.NewEncoding("abcdefghijklmnopqrstuvwxyz234567").WithPadding(base32.NoPadding)
	secretEncoding = base64.RawURLEncoding
)

// NewKey draws a key: its key_id, the SHA-256 of its secret to store, and the full key, which is
// shown once and never stored.
func NewKey() (keyID string, secretSHA256 []byte, full string, err error) {
	var id [8]byte // 64 bits, of which the 12 characters keep 60
	if _, err := rand.Read(id[:]); err != nil {
		return "", nil, "", fmt.Errorf("new key: %w", err)
	}
	var secret [secretBytes]byte
	if _, err := rand.Read(secret[:]); err != nil {
		return "", nil, "", fmt.Errorf("new key: %w", err)
	}
	keyID = keyIDEncoding.EncodeToString(id[:])[:keyIDLength]
	encoded := secretEncoding.EncodeToString(secret[:])
	sum := sha256.Sum256([]byte(encoded))
	return keyID, sum[:], keyPrefix + keyID + "_" + encoded, nil
}

// parseKey reads a presented key by its fixed layout. It reports false for anything that is not
// exactly a key, and returns the key_id and the SHA-256 of the secret part.
func parseKey(v string) (keyID string, secretSHA256 [sha256.Size]byte, ok bool) {
	if len(v) != keyLength || !strings.HasPrefix(v, keyPrefix) || v[len(keyPrefix)+keyIDLength] != '_' {
		return "", secretSHA256, false
	}
	keyID = v[len(keyPrefix) : len(keyPrefix)+keyIDLength]
	secret := v[len(keyPrefix)+keyIDLength+1:]
	for _, c := range keyID {
		if (c < 'a' || c > 'z') && (c < '2' || c > '7') {
			return "", secretSHA256, false
		}
	}
	raw, err := secretEncoding.DecodeString(secret)
	if err != nil || len(raw) != secretBytes {
		return "", secretSHA256, false
	}
	return keyID, sha256.Sum256([]byte(secret)), true
}
