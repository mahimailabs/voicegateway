package seal

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"errors"
	"fmt"
)

// version prefixes every sealed value, so the format can change without guessing.
const version byte = 1

// KeySize is the length of the key in bytes (AES-256).
const KeySize = 32

// ErrOpen means a sealed value did not open: wrong key, wrong record, or tampered data.
var ErrOpen = errors.New("sealed value did not open: wrong VOICEGATEWAY_SECRET_KEY, wrong record or damaged data")

// Sealer seals and opens secrets with one key.
type Sealer struct {
	aead cipher.AEAD
}

// New returns a Sealer for a 32-byte key.
func New(key []byte) (*Sealer, error) {
	if len(key) != KeySize {
		return nil, fmt.Errorf("seal key must be %d bytes, got %d", KeySize, len(key))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("seal cipher: %w", err)
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("seal GCM: %w", err)
	}
	return &Sealer{aead: aead}, nil
}

// Seal encrypts plaintext for the record named by context, for example "default/my-project".
func (s *Sealer) Seal(plaintext []byte, context string) ([]byte, error) {
	nonce := make([]byte, s.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, fmt.Errorf("seal nonce: %w", err)
	}
	out := make([]byte, 0, 1+len(nonce)+len(plaintext)+s.aead.Overhead())
	out = append(out, version)
	out = append(out, nonce...)
	return s.aead.Seal(out, nonce, plaintext, []byte(context)), nil
}

// Open decrypts a value sealed for the same context. Any mismatch returns ErrOpen.
func (s *Sealer) Open(sealed []byte, context string) ([]byte, error) {
	n := s.aead.NonceSize()
	if len(sealed) < 1+n+s.aead.Overhead() || sealed[0] != version {
		return nil, ErrOpen
	}
	plain, err := s.aead.Open(nil, sealed[1:1+n], sealed[1+n:], []byte(context))
	if err != nil {
		return nil, ErrOpen
	}
	return plain, nil
}
