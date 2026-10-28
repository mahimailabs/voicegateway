package join

import (
	"context"
	"errors"
	"fmt"

	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/webhook"
	"google.golang.org/protobuf/encoding/protojson"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// mediaFindings runs after durable event insertion, before stream acknowledgement. A finding whose
// call was deleted after the join (by retention, or by hand while the call was live) is kept
// without the call, so the stream entry can still be acknowledged instead of failing on every retry.
func (j *Joiner) mediaFindings(ctx context.Context, rows []store.Event) error {
	for _, row := range rows {
		if row.Kind != string(event.KindLiveKitWebhook) {
			continue
		}
		var ev livekit.WebhookEvent
		if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(row.Payload, &ev); err != nil {
			continue
		}
		finding := store.MediaFinding{OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, EventID: ev.GetId(), CallID: row.CallID, At: ptr(seconds(ev.GetCreatedAt()))}
		if ev.GetId() == "" {
			continue
		}
		switch ev.GetEvent() {
		case webhook.EventEgressEnded:
			info := ev.GetEgressInfo()
			if info == nil || info.GetEgressId() == "" {
				continue
			}
			switch info.GetStatus() {
			case livekit.EgressStatus_EGRESS_FAILED, livekit.EgressStatus_EGRESS_ABORTED, livekit.EgressStatus_EGRESS_LIMIT_REACHED:
			default:
				continue
			}
			finding.Layer, finding.ComponentID, finding.Status = "egress", info.GetEgressId(), info.GetStatus().String()
		case webhook.EventIngressEnded:
			info := ev.GetIngressInfo()
			if info == nil || info.GetIngressId() == "" || info.GetState() == nil || info.GetState().GetStatus() != livekit.IngressState_ENDPOINT_ERROR {
				continue
			}
			finding.Layer, finding.ComponentID, finding.Status = "ingress", info.GetIngressId(), info.GetState().GetStatus().String()
		default:
			continue
		}
		err := j.Calls.PutMediaFinding(ctx, finding)
		if errors.Is(err, store.ErrNotFound) && finding.CallID != "" {
			finding.CallID = ""
			err = j.Calls.PutMediaFinding(ctx, finding)
		}
		if err != nil {
			return fmt.Errorf("store media finding: %w", err)
		}
	}
	return nil
}
