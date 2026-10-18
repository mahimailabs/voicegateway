// Package sink is the worker's writer: it reads the ingest stream in a consumer group, joins each
// envelope to its call and stores it in ClickHouse in batches (decision 0007, ARCHITECTURE.md Data Flow 3 and 4).
//
// Rules: an entry is acknowledged only after its call writes and its rows are stored, or it is
// already stored; an event
// id seen before is skipped, across restarts (0012); an entry that cannot be decoded, or that
// ClickHouse refuses for its data, is moved to the dead-letter stream, never dropped; any other
// failure (down, busy, slow) is retried and nothing is acknowledged meanwhile. LiveKit's TURN
// password, and every Egress and Ingress credential, stream key and personal field, are removed
// from webhook bodies before they are stored and before they are dead-lettered.
package sink
