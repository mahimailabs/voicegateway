package store

import (
	"errors"
	"strings"
	"unicode"
	"unicode/utf8"
)

var (
	ErrInvalidProfile         = errors.New("name must contain at most 100 characters and no control characters")
	ErrProfileConflict        = errors.New("profile changed since it was read")
	ErrInvalidProfileRevision = errors.New("invalid profile revision")
)

type AccountProfile struct {
	Name     *string `json:"name"`
	Revision int64   `json:"revision"`
}

func ProfileName(value string) (*string, error) {
	if !utf8.ValidString(value) || strings.ContainsFunc(value, unicode.IsControl) {
		return nil, ErrInvalidProfile
	}
	value = strings.TrimSpace(value)
	if utf8.RuneCountInString(value) > 100 {
		return nil, ErrInvalidProfile
	}
	if value == "" {
		return nil, nil
	}
	return &value, nil
}
