package api_test

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"

	"github.com/livekit/protocol/auth"
	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/rpc"
	"github.com/livekit/protocol/webhook"
)

// Verify the server-emitted, signed webhook rather than hand-constructing a fixture body.
// The native IOInfo bus supplies status exactly as the ingress service reports it.
func TestIngressErrorEmitsSignedTerminalWebhook(t *testing.T) {
	events := make(chan *livekit.WebhookEvent, 8)
	receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ev, err := webhook.ReceiveWebhookEvent(r, auth.NewSimpleKeyProvider(devKey, devSecret))
		if err != nil {
			http.Error(w, "signature rejected", http.StatusUnauthorized)
			return
		}
		select {
		case events <- ev:
		default:
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer receiver.Close()
	_, portText, err := net.SplitHostPort(receiver.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(portText)
	if err != nil {
		t.Fatal(err)
	}
	f := newFixtureWithWebhook(t, port)
	svc := f.ioService(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	info := &livekit.IngressInfo{IngressId: "IN_error_webhook", StreamKey: "test-ingress-webhook-key", Name: "native error verification", RoomName: "ingress-verification", InputType: livekit.IngressInput_RTMP_INPUT, Reusable: true, State: &livekit.IngressState{Status: livekit.IngressState_ENDPOINT_INACTIVE}}
	if _, err := svc.CreateIngress(ctx, info); err != nil {
		t.Fatal(err)
	}
	for _, status := range []livekit.IngressState_Status{livekit.IngressState_ENDPOINT_PUBLISHING, livekit.IngressState_ENDPOINT_ERROR, livekit.IngressState_ENDPOINT_PUBLISHING} {
		state := &livekit.IngressState{Status: status, UpdatedAt: time.Now().UnixNano()}
		if status == livekit.IngressState_ENDPOINT_ERROR {
			state.Error = "native test failure"
		}
		if _, err := svc.UpdateIngressState(ctx, &rpc.UpdateIngressStateRequest{IngressId: info.IngressId, State: state}); err != nil {
			t.Fatal(err)
		}
		wanted := webhook.EventIngressStarted
		if status == livekit.IngressState_ENDPOINT_ERROR {
			wanted = webhook.EventIngressEnded
		}
		select {
		case ev := <-events:
			if ev.Event != wanted || ev.GetIngressInfo().GetIngressId() != info.IngressId || ev.GetIngressInfo().GetState().GetStatus() != status || ev.Id == "" || ev.CreatedAt <= 0 {
				t.Fatalf("unexpected native webhook type/status: %s %s", ev.Event, ev.GetIngressInfo().GetState().GetStatus())
			}
		case <-ctx.Done():
			t.Fatal("timed out waiting for signed native ingress webhook")
		}
	}
}
