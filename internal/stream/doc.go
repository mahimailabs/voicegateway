// Package stream is the Redis stream between web and worker, the only contract between them
// (decision 0007, CLAUDE.md Boundaries). web appends envelopes with a Producer; worker reads them in
// a consumer group with a Consumer and acknowledges each one only after it is stored.
package stream
