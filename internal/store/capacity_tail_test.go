package store

import "testing"

func TestTailOf(t *testing.T) {
	tests := []struct {
		name                     string
		in                       []float64
		median, p95, share, slow float64
	}{
		{name: "one answer", in: []float64{0.6}, median: 0.6, p95: 0.6, share: 0, slow: 0.6},
		{name: "the slow end shows past the median", in: []float64{0.5, 0.6, 0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 1.4, 2.7}, median: 0.6, p95: 1.4, share: 0.1, slow: 2.7},
		{name: "exactly one second is not slow", in: []float64{1, 1}, median: 1, p95: 1, share: 0, slow: 1},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := TailOf(tt.in)
			if *got.Median != tt.median || *got.P95 != tt.p95 || *got.SlowShare != tt.share || *got.Slowest != tt.slow || got.Count != len(tt.in) {
				t.Errorf("TailOf = median %v p95 %v share %v slowest %v", *got.Median, *got.P95, *got.SlowShare, *got.Slowest)
			}
		})
	}
	if got := TailOf(nil); got.Median != nil || got.P95 != nil || got.SlowShare != nil || got.Slowest != nil {
		t.Errorf("no answers must be unknown, not zero: %+v", got)
	}
}
