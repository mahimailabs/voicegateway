package api_test

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/twitchtv/twirp"

	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// The guarded LiveKit dial (VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY) builds its own Twirp clients
// and signs each call itself. Against a real livekit-server --dev, every call it makes answers what
// the SDK's own client answers, with the right secret and with a wrong one. The guard is given the
// self-hosted rules here, since the test server is on a private address.
func TestGuardedLiveKitDialAnswersLikeTheSDK(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	room := f.createRoom(t, "call-guarded").GetName()
	if _, err := lksdk.NewSIPClient(f.url, devKey, devSecret).CreateSIPInboundTrunk(ctx, &livekit.CreateSIPInboundTrunkRequest{
		Trunk: &livekit.SIPInboundTrunkInfo{Name: "guarded", Numbers: []string{"+15550002222"}},
	}); err != nil {
		t.Fatal(err)
	}
	guarded := scrape.DialThrough(scrape.NewWorkerClient(false))
	calls := []struct {
		name string
		call func(scrape.LiveKit) (int, error)
	}{
		{"ListRooms", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListRooms(ctx); return len(r), err }},
		{"ListParticipants", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListParticipants(ctx, room); return len(r), err }},
		{"ListDispatch", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListDispatch(ctx, room); return len(r), err }},
		{"ListSIPInboundTrunk", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListSIPInboundTrunk(ctx); return len(r), err }},
		{"ListSIPOutboundTrunk", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListSIPOutboundTrunk(ctx); return len(r), err }},
		{"ListSIPDispatchRule", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListSIPDispatchRule(ctx); return len(r), err }},
		{"ListPhoneNumbers", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListPhoneNumbers(ctx); return len(r), err }},
		{"ListEgress", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListEgress(ctx); return len(r), err }},
		{"ListIngress", func(lk scrape.LiveKit) (int, error) { r, err := lk.ListIngress(ctx); return len(r), err }},
	}
	code := func(err error) twirp.ErrorCode {
		var te twirp.Error
		if errors.As(err, &te) {
			return te.Code()
		}
		if err != nil {
			return "not a twirp error"
		}
		return ""
	}
	for _, secret := range []struct{ name, value string }{{"right secret", devSecret}, {"wrong secret", "not-the-dev-secret"}} {
		sdk, ours := scrape.DialSDK(f.url, devKey, secret.value), guarded(f.url, devKey, secret.value)
		for _, c := range calls {
			t.Run(secret.name+"/"+c.name, func(t *testing.T) {
				wantN, wantErr := c.call(sdk)
				gotN, gotErr := c.call(ours)
				if gotN != wantN || code(gotErr) != code(wantErr) {
					t.Errorf("guarded = %d items, %v; SDK = %d items, %v", gotN, gotErr, wantN, wantErr)
				}
				if secret.value == devSecret && c.name == "ListRooms" && gotN == 0 {
					t.Error("the guarded ListRooms saw no room")
				}
				if secret.value != devSecret && code(gotErr) != twirp.Unauthenticated {
					t.Errorf("a wrong secret answered %v, want unauthenticated", gotErr)
				}
			})
		}
	}
}

// A region outage is a failed observation, not an empty room list. The next poll
// must recover using the same scraper after connectivity returns.
func TestGuardedLiveKitOutageRecoversOnNextPoll(t *testing.T) {
	f := newFixture(t)
	f.addProject("outage", devSecret)
	room := f.createRoom(t, "call-during-outage")
	dial := scrape.DialThrough(scrape.NewWorkerClient(false))
	s := f.scraper(nil)
	s.Dial = dial
	f.sample(t, s, 1)
	before := f.read(t, "outage")
	if before.SourceStatus == nil || before.SourceStatus.LastGoodReadAt == nil {
		t.Fatal("initial read did not succeed")
	}
	lastGood := *before.SourceStatus.LastGoodReadAt
	closed := httptest.NewServer(http.NotFoundHandler())
	closed.Close()
	s.Dial = func(_ string, key, secret string) scrape.LiveKit { return dial(closed.URL, key, secret) }
	time.Sleep(every)
	f.sample(t, s, 1)
	failed := f.read(t, "outage")
	if failed.SourceStatus == nil || failed.SourceStatus.ErrorClass == nil || *failed.SourceStatus.ErrorClass != store.ErrorUnreachable {
		t.Fatalf("outage status = %+v, want unreachable", failed.SourceStatus)
	}
	if failed.room(room.GetSid()) == nil || failed.SourceStatus.LastGoodReadAt == nil || !failed.SourceStatus.LastGoodReadAt.Equal(lastGood) {
		t.Fatal("failed read discarded the last observed room or replaced its observation time")
	}
	s.Dial = dial
	time.Sleep(every)
	f.sample(t, s, 1)
	recovered := f.read(t, "outage")
	if recovered.SourceStatus == nil || recovered.SourceStatus.ErrorClass != nil || recovered.SourceStatus.LastGoodReadAt == nil || !recovered.SourceStatus.LastGoodReadAt.After(lastGood) {
		t.Fatalf("next poll did not recover: %+v", recovered.SourceStatus)
	}
}
