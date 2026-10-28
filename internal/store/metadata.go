package store

import (
	"errors"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"
)

var (
	ErrInvalidMetadata  = errors.New("invalid metadata revision or tags: at most 32 tags of 1 to 64 characters without control characters")
	ErrMetadataSnapshot = errors.New("call metadata changed; restart pagination")
	ErrMetadataConflict = errors.New("metadata changed since it was read")
)

type CallMetadata struct {
	Tags     []string `json:"tags"`
	Archived bool     `json:"archived"`
	Revision int64    `json:"revision"`
}

// NormalizeTags preserves the console's lowercase tag convention without treating room names as IDs.
func NormalizeTags(tags []string) ([]string, error) {
	if len(tags) > 32 {
		return nil, ErrInvalidMetadata
	}
	out := make([]string, 0, len(tags))
	for _, tag := range tags {
		if !utf8.ValidString(tag) || strings.ContainsFunc(tag, unicode.IsControl) {
			return nil, ErrInvalidMetadata
		}
		tag = strings.ToLower(strings.TrimSpace(tag))
		if tag == "" || utf8.RuneCountInString(tag) > 64 {
			return nil, ErrInvalidMetadata
		}
		out = append(out, tag)
	}
	slices.Sort(out)
	return slices.Compact(out), nil
}
