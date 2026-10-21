// Package contract holds the language-neutral SDK wire contract (docs/specs/python-sdk.md): the
// JSON Schema every SDK batch must satisfy and the example batches that every SDK's tests and the
// engine's handler tests share as conformance fixtures.
package contract

import "embed"

// SchemaV1 is the JSON Schema of a version 1 batch.
//
//go:embed v1.schema.json
var SchemaV1 []byte

// Examples holds examples/valid/*.json, which every engine accepts, and examples/invalid/*.json,
// which every engine refuses.
//
//go:embed examples
var Examples embed.FS
