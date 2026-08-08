---
title: "Node metrics"
description: "The Prometheus scrape that fills node_samples: target formats, when it's read, and what the shipped Grafana panels can and cannot show."
---
A background worker scrapes Prometheus endpoints on the SFU fleet into `node_samples`: file descriptors, CPU, memory, SIP answer latency, Redis health. It is the source for per-node capacity evidence. For how this fits the agent and SIP layers, see [What you can profile](/guide/what-you-can-profile).

## Turning it on

The scrape is off by default. Set `VOICEGW_NODE_SCRAPE_TARGETS` to a comma-separated list of `source:name=url` entries before starting the collector:

```bash
export VOICEGW_NODE_SCRAPE_TARGETS="livekit-server:sfu-1=http://10.0.0.4:6789/metrics,livekit-sip:sip-1=http://10.0.0.5:8082/metrics,node-exporter:sfu-1=http://10.0.0.4:9100/metrics,redis-exporter:cache-1=http://10.0.0.6:9121/metrics"
```

`source` is one of four values: `livekit-server`, `livekit-sip`, `node-exporter`, or `redis-exporter`. `name` is the node the samples are filed under; reusing one `name` across sources (`sfu-1` above) puts an SFU's counters and its host's file descriptors on one time axis.

## Targets from a file

`VOICEGW_NODE_SCRAPE_TARGETS_FILE` points at a file with the same `source:name=url` grammar, one entry per line or comma-separated. Unlike the environment variable, **it is re-read on every tick**, so a fleet whose addresses change (autoscaling, a replaced instance) needs no restart: a timer, a config run, or a person rewrites the file, and the next tick picks it up.

If both are set, the file wins, even when currently empty: an empty file differs from no file, and a stale-variable fallback would scrape replaced addresses.

## When it's read

Whether to build the worker is decided once, at `voicegw serve` startup. Exporting `VOICEGW_NODE_SCRAPE_TARGETS` (or `_FILE`) afterward does nothing until restarted.

It also runs only inside that long-lived process (including the daemon's `start`/`restart`), never inside a one-shot CLI command: `voicegw livekit check`, `voicegw livekit latency`, and `voicegw loadtest report` all exit without touching `node_samples`. Poll cadence is `workers.node_scrape_interval_seconds` in [`voicegw.yaml`](/configuration/voicegw-yaml) (default 15s).

## A bad entry costs one target, not the process

An entry that fails to parse as `source:name=url`, or names a `source` outside the four valid values, is dropped with a log warning rather than raising: the worker shares a process with the dashboard, so a typo can't take it down. The cost: a typo'd source silently loses that target every tick, with nothing louder than the warning line.

## What gets collected

- **`livekit-server` / `livekit-sip`**: their own Go runtime and process metrics (heap, goroutines, open/max file descriptors, start time, CPU seconds, resident memory). `livekit-server` adds rooms, participants, packet totals; `livekit-sip` adds SIP call/INVITE counters, the join/check answer-latency histograms, and RTP packet counts split by direction.
- **`node-exporter`**: the host: CPU, memory, load, file-descriptor headroom, UDP port headroom, cloud-NIC allowance-exceeded counters, NIC throughput, kernel OOM kills, and, only where a textfile collector publishes it, the RTP media-port range and how much is in use. Also its own process descriptors: node-exporter's file handles, not any service's.
- **`redis-exporter`**: six series (`redis_up`, rejected connections, memory used/max, blocked clients, evicted keys), nothing about the fleet's CPU or memory.

A series a source doesn't expose stores NULL, never 0. A target that fails to answer still writes a row, with an outcome (`timeout`, `unreachable`, `http_error`, `too_large`, `unparseable`) and NULL values.

## Watching it in Grafana

`deploy/grafana/voicegateway-load-test.json` renders 14 panels. Ten have a series behind them: establishment rate, CPU and memory utilisation, file descriptors against the limit, Go heap and goroutines, rooms/participants/active calls, Redis reachability, Redis memory against its limit, and Redis blocked clients.

Four render as **NOT MEASURED** text instead of a graph: CPU per core, RTP port usage, ENA packets-per-second allowance, and conntrack occupancy. Two are accurate: no column sums per-core CPU (`cpu_seconds_total` is scraped already collapsed across cores and modes), and none reads live conntrack-table size (`nf_conntrack_entries` isn't wired). The other two predate columns that exist today: `media_ports_total` / `media_ports_in_use` carry RTP port headroom where the textfile collector runs, and `ethtool_pps_allowance_exceeded` is the same cloud-NIC packets-per-second ceiling the panel calls `ena_pps_allowance_exceeded`. Both are collected where a target publishes them; the dashboard just doesn't chart them yet.

For a capacity ramp that feeds this table, see [Distributed SFU probers](/deployment/distributed-sfu); to correlate a sample with an external SIP load run, see [`voicegw loadtest`](/cli/loadtest).
