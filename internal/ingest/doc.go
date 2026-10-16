// Package ingest is web's front door for data: it verifies what LiveKit sends against the project's
// own LiveKit key and secret, then appends it to the stream and answers (decisions 0007, 0014).
//
// Rule: nothing reaches the stream unverified, and no body is ever logged.
package ingest
