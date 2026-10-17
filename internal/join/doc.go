// Package join resolves each stored event to its call (phase 04a, decision 0012): webhooks and
// session reports by room SID, spans by their own room and job ids, and a trace's other spans
// through the trace id. It writes the call index in Postgres and sets, on each event row, the call
// id, the decoded spans, the trace links and any unmatched record, which the sink then stores.
//
// Rules: a time is taken only from a source's reported value, never the time of processing; an
// event that resolves to no call is stored with an empty call id and counted, never dropped; a
// span's resource attributes are never read for its ids, since LiveKit Agents sets them once per
// process; and a span that starts after the call its ids name has ended is not joined, since
// LiveKit Agents falls back to the last job's ids outside a job.
package join
