package store

import (
	"errors"
	"math"
)

var (
	ErrInvalidSettingsRevision = errors.New("invalid project settings revision")
	ErrSettingsConflict        = errors.New("project settings changed since they were read")
	ErrInvalidThresholds       = errors.New("thresholds must be finite and between 100 and 60000 milliseconds")
)

type Thresholds struct {
	AnswerMs   float64 `json:"answerMs"`
	JoinMs     float64 `json:"joinMs"`
	TTSStartMs float64 `json:"ttsStartMs"`
}

func (t Thresholds) Validate() error {
	for _, v := range []float64{t.AnswerMs, t.JoinMs, t.TTSStartMs} {
		if math.IsNaN(v) || math.IsInf(v, 0) || v < 100 || v > 60000 {
			return ErrInvalidThresholds
		}
	}
	return nil
}

type ProjectSettings struct {
	Thresholds Thresholds `json:"thresholds"`
	Revision   int64      `json:"revision"`
	// AllowedOrigins are the sites allowed to frame the project's widgets; nil is not configured,
	// which allows none.
	AllowedOrigins []string `json:"-"`
	// AllowedOriginsRevision versions AllowedOrigins alone, so saving sites leaves Revision, the
	// thresholds' version, alone.
	AllowedOriginsRevision int64 `json:"-"`
}
