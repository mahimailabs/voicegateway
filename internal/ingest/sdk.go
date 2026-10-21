package ingest

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/sdk/contract"
)

// maxSDKBatch bounds an SDK batch, compressed or not (sdk/contract/v1.schema.json allows 1,000
// events).
const maxSDKBatch = 1 << 20

var (
	sdkSchemaOnce sync.Once
	sdkSchema     *jsonschema.Schema
	errSDKSchema  error
)

// sdkBatchSchema compiles the contract's schema once. Formats are asserted, so an event time that
// is not RFC 3339 is refused like any other shape error.
func sdkBatchSchema() (*jsonschema.Schema, error) {
	sdkSchemaOnce.Do(func() {
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(contract.SchemaV1))
		if err != nil {
			errSDKSchema = fmt.Errorf("sdk schema: %w", err)
			return
		}
		c := jsonschema.NewCompiler()
		c.AssertFormat()
		if err := c.AddResource("v1.schema.json", doc); err != nil {
			errSDKSchema = fmt.Errorf("sdk schema: %w", err)
			return
		}
		sdkSchema, errSDKSchema = c.Compile("v1.schema.json")
	})
	return sdkSchema, errSDKSchema
}

// SDKEvents answers a VoiceGateway SDK batch (docs/specs/python-sdk.md): verified like an agent
// upload, checked against the wire contract's schema, and appended as one envelope named by its
// batch id, so a retried batch is stored once.
func (u *Uploads) SDKEvents(w http.ResponseWriter, r *http.Request) {
	keys, ok := u.verify(w, r)
	if !ok {
		return
	}
	body, err := readBody(http.MaxBytesReader(w, r.Body, maxSDKBatch), r.Header.Get("Content-Encoding"), maxSDKBatch)
	if err != nil {
		u.Log.Warn("upload: unreadable SDK batch", "project", keys.project.ID, "reason", err.Error())
		u.refuseBody(r, err)
		http.Error(w, "unreadable body", statusFor(err))
		return
	}
	batchID, err := validSDKBatch(body)
	if err != nil {
		u.Log.Warn("upload: SDK batch refused", "project", keys.project.ID, "rule", schemaRule(err))
		http.Error(w, "batch does not match sdk/contract/v1.schema.json", http.StatusBadRequest)
		return
	}
	if !u.appendEnvelope(w, r, keys, event.Envelope{ID: "sdk:" + batchID, Kind: event.KindSDKEvents, Payload: body}) {
		return
	}
	u.Outcome.Accepted(r.Context())
	w.WriteHeader(http.StatusAccepted)
}

// validSDKBatch checks body against the contract and returns its batch id.
func validSDKBatch(body []byte) (string, error) {
	sch, err := sdkBatchSchema()
	if err != nil {
		return "", err
	}
	doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(body))
	if err != nil {
		return "", fmt.Errorf("not JSON: %w", err)
	}
	if err := sch.Validate(doc); err != nil {
		return "", err
	}
	var b event.SDKBatch
	if err := json.Unmarshal(body, &b); err != nil {
		return "", err
	}
	return b.BatchID, nil
}

// schemaRule names the schema rule a batch broke, from the contract's own schema: a validation
// error's text quotes the body, which is never logged.
func schemaRule(err error) string {
	var v *jsonschema.ValidationError
	if !errors.As(err, &v) {
		return "not JSON"
	}
	for len(v.Causes) > 0 {
		v = v.Causes[0]
	}
	return v.SchemaURL
}
