package seal

import (
	"bytes"
	"errors"
	"testing"
)

func key(b byte) []byte { return bytes.Repeat([]byte{b}, KeySize) }

func TestOpenWithWrongKey(t *testing.T) {
	right, err := New(key(1))
	if err != nil {
		t.Fatal(err)
	}
	wrong, err := New(key(2))
	if err != nil {
		t.Fatal(err)
	}
	sealed, err := right.Seal([]byte("lk-secret"), "default/my-project")
	if err != nil {
		t.Fatal(err)
	}
	tampered := bytes.Clone(sealed)
	tampered[len(tampered)-1] ^= 0xff

	tests := []struct {
		name    string
		sealer  *Sealer
		sealed  []byte
		context string
		want    string
		wantErr bool
	}{
		{name: "same key and record", sealer: right, sealed: sealed, context: "default/my-project", want: "lk-secret"},
		{name: "different key", sealer: wrong, sealed: sealed, context: "default/my-project", wantErr: true},
		{name: "copied to another record", sealer: right, sealed: sealed, context: "default/other", wantErr: true},
		{name: "tampered", sealer: right, sealed: tampered, context: "default/my-project", wantErr: true},
		{name: "truncated", sealer: right, sealed: sealed[:5], context: "default/my-project", wantErr: true},
		{name: "empty", sealer: right, sealed: nil, context: "default/my-project", wantErr: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := tt.sealer.Open(tt.sealed, tt.context)
			if tt.wantErr {
				if !errors.Is(err, ErrOpen) || got != nil {
					t.Fatalf("Open() = %q, %v; want nil, ErrOpen", got, err)
				}
				return
			}
			if err != nil || string(got) != tt.want {
				t.Fatalf("Open() = %q, %v; want %q", got, err, tt.want)
			}
		})
	}
}

func TestSealNeverRepeatsOrLeaks(t *testing.T) {
	s, err := New(key(1))
	if err != nil {
		t.Fatal(err)
	}
	a, _ := s.Seal([]byte("lk-secret"), "default/p")
	b, _ := s.Seal([]byte("lk-secret"), "default/p")
	if bytes.Equal(a, b) {
		t.Error("two seals of the same secret are identical; the nonce is not random")
	}
	if bytes.Contains(a, []byte("lk-secret")) {
		t.Error("sealed value contains the plaintext")
	}
}

func TestNewRejectsWrongKeySize(t *testing.T) {
	if _, err := New(make([]byte, 16)); err == nil {
		t.Error("New() accepted a 16-byte key")
	}
}
