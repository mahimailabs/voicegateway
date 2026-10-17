// Package scrape is the worker's reader of each project's LiveKit server (ARCHITECTURE.md Data Flow
// 6, decision 0016, docs/specs/phase05-livekit-layers.md). It reads through LiveKit's own Go
// clients with the project's key and secret, and stores the redacted projections the console
// contract describes.
//
// Rules: only the contract's projection is stored, never a raw LiveKit message, an identity, a name,
// metadata or a secret; a time LiveKit did not report stays null; a failed read is stored as failed,
// with its error class, never as an empty result; one worker reads a project's layer per interval.
package scrape
