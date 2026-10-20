package scrape

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type liveSamples struct {
	Samples
	fail   bool
	stored bool
}

func (s *liveSamples) StoreRead(context.Context, store.LayerRead, []store.RoomSample, []store.ParticipantSample) error {
	if s.fail {
		return errors.New("database unavailable")
	}
	s.stored = true
	return nil
}

func TestLiveHintFollowsDurableSourceRead(t *testing.T) {
	for _, fail := range []bool{false, true} {
		for _, sourceOK := range []bool{false, true} {
			samples := &liveSamples{fail: fail}
			count := 0
			scraper := &Scraper{Samples: samples, Timeout: time.Second, Changed: func(_ context.Context, org, project string) {
				if !samples.stored {
					t.Error("hint before storage")
				}
				if org != "org" || project != "p" {
					t.Error("wrong scope")
				}
				count++
			}}
			err := scraper.store(context.Background(), store.LayerRead{OrganisationID: "org", ProjectID: "p", OK: sourceOK}, nil, nil)
			if (err != nil) != fail {
				t.Fatalf("storage error: %v", err)
			}
			if want := map[bool]int{false: 1, true: 0}[fail]; count != want {
				t.Fatalf("hints=%d want=%d", count, want)
			}
		}
	}
}
