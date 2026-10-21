package event

import (
	"encoding/json"
	"time"
)

// SDKBatch is the Payload of KindSDKEvents: one batch of a VoiceGateway SDK, as
// sdk/contract/v1.schema.json defines it. web validates every batch against that schema before it
// is appended, so a decoded batch has a room SID or a job id and at least one event.
type SDKBatch struct {
	Version   int          `json:"version"`
	BatchID   string       `json:"batchId"`
	SDK       SDKComponent `json:"sdk"`
	Framework SDKComponent `json:"framework"`
	Call      SDKCall      `json:"call"`
	Events    []SDKEvent   `json:"events"`
}

// SDKComponent names the SDK or the framework it observes, with its version.
type SDKComponent struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

// SDKCall is the call a batch belongs to; nil is not reported.
type SDKCall struct {
	JobID    *string `json:"jobId"`
	RoomName *string `json:"roomName"`
	RoomSID  *string `json:"roomSid"`
}

// SDKEvent is one event; Data's shape depends on Type (the schema's $defs). An unknown type is kept
// and ignored, so an older engine takes a newer SDK.
type SDKEvent struct {
	Type string          `json:"type"`
	At   time.Time       `json:"at"`
	Data json.RawMessage `json:"data"`
}

// SDK event types the engine reads.
const (
	SDKState   = "state"
	SDKMessage = "message"
	SDKUsage   = "usage"
	SDKTool    = "tool"
	SDKError   = "error"
	SDKFields  = "fields"
	SDKClose   = "close"
)

// SDKStateData is the data of a state event.
type SDKStateData struct {
	Actor string `json:"actor"`
	State string `json:"state"`
}

// SDKMessageData is the data of a message event.
type SDKMessageData struct {
	Role        string  `json:"role"`
	Text        *string `json:"text"`
	Interrupted *bool   `json:"interrupted"`
}

// SDKFieldsData is the data of a fields event.
type SDKFieldsData struct {
	Fields map[string]string `json:"fields"`
}
