package join

import (
	"context"
	"fmt"

	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/webhook"
	"google.golang.org/protobuf/encoding/protojson"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// recordingFindings runs after durable event insertion, before stream acknowledgement.
func (j *Joiner) recordingFindings(ctx context.Context, rows []store.Event) error {
	for _, row := range rows {
		if row.Kind != string(event.KindLiveKitWebhook) {
			continue
		}
		var ev livekit.WebhookEvent
		if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(row.Payload, &ev); err != nil {
			continue
		}
		info := ev.GetEgressInfo()
		if ev.GetEvent() != webhook.EventEgressEnded || info == nil || ev.GetId() == "" || info.GetEgressId() == "" {
			continue
		}
		switch info.GetStatus() {
		case livekit.EgressStatus_EGRESS_FAILED, livekit.EgressStatus_EGRESS_ABORTED, livekit.EgressStatus_EGRESS_LIMIT_REACHED:
		default:
			continue
		}
		if err := j.Calls.PutRecordingFinding(ctx, store.RecordingFinding{
			OrganisationID: row.OrganisationID, ProjectID: row.ProjectID,
			EventID: ev.GetId(), EgressID: info.GetEgressId(), CallID: row.CallID,
			Status: info.GetStatus().String(), At: ptr(seconds(ev.GetCreatedAt())),
		}); err != nil {
			return fmt.Errorf("store recording finding: %w", err)
		}
	}
	return nil
}
