---
title: Default MCP Tools
description: The ten VoiceGateway MCP tools visible without VOICEGW_MCP_ADMIN. Health, cost, latency, and log reads, plus project, model, and rate card tools.
---

These ten tools are what a connected agent sees with no extra configuration. See [Admin tools](/mcp/tools-admin) for the fifteen tools gated behind `VOICEGW_MCP_ADMIN=1`.

## Observability

### get_health

No arguments. Never errors, so it's a cheap first call. Returns `version`, `uptime_seconds`, `status` (always `"ok"`), `db_configured`, `project_count`, `provider_count`, and `observability` (`{latency_tracking, cost_tracking, request_logging}` flags from `voicegw.yaml`).

### get_costs

Args: `period` (`"today"` default, `"week"`, `"month"`, `"all"`), `project` (optional filter). Returns `total_usd`, `by_provider` and `by_model` (each keyed dict of `{cost, requests}`), and `by_project` (populated only when `project` is omitted). Derived from the SQLite request log; every value is `0` if storage is disabled.

### get_latency_stats

Args: `period` (`"today"` default, `"week"`, `"month"`), `project`, `modality` (`"stt"` | `"llm"` | `"tts"`). Returns `overall` and `by_model`, each carrying `ttfb_percentiles` / `latency_percentiles` (a `{p50, p95, p99}`-shaped dict; the exact percentiles come from `latency.percentiles` in `voicegw.yaml`) plus `avg_ttfb_ms`, `avg_latency_ms`, `request_count`.

### get_logs

Args: `project`, `modality`, `model_id`, `status` (`"success"` | `"error"` | `"fallback"`), `limit` (1-1000, default 50). Returns up to `limit` request rows, newest first, each with `timestamp`, `project`, `modality`, `model_id`, `provider`, `cost_usd`, `pricing_source`, `ttfb_ms`, `total_latency_ms`, `status`, `error_message`, plus rating fields (`rated_price_usd`, `rate_rule`; see [Rating](/architecture/rating)) and `session_id`/`tenant_id`/`agent_id`.

## Models

### list_models

Args: `modality`, `provider_id`, `enabled_only` (default `true`). Returns `models` (each `{model_id, modality, provider_id, model_name, default_voice, source: "yaml"|"db", enabled}`, plus `display_name`/`default_language` on DB-managed rows) and `count`.

## Projects

A project is an attribution and cost-control scope, not a routing config: VoiceGateway meters the STT/LLM/TTS instances your agent attaches, so a project carries no model assignment. See [Projects](/configuration/projects) for the full field reference and the budget-enforcement caveat.

### list_projects / get_project

`list_projects` takes no arguments; `get_project` requires `project_id` (raises `PROJECT_NOT_FOUND` if unknown). Both return the project's config plus `budget_status` (`"ok"` / `"warning"` at ≥80% of `daily_budget` / `"exceeded"` at ≥100%, computed from today's recorded spend) and `today_spend`/`today_requests`. `get_project` adds `week_spend`/`week_requests`.

### create_project

Args: `project_id`, `name`, `description` (default `""`), `daily_budget` (default `0`, unlimited), `budget_action` (`"warn"` default, `"throttle"`, `"block"`), `tags`. `budget_action` is stored and returned, but doesn't change gateway behavior by itself: see the [budget caveat](/configuration/projects#budgets). Raises `PROJECT_ALREADY_EXISTS` or `VALIDATION_ERROR` (cost tracking disabled).

## Rate card

See [Rating](/architecture/rating) for what a rate card is and how `cost_plus` / `fixed` rules resolve.

### get_rate_card

No arguments. Returns `default_markup`, the merged effective `rules`, and the raw DB `overrides` (each carries a `rule_id`).

### set_rate_card_override

Args: a scope (`modality`, `provider`, `model`, `plan`, each defaulting to `"*"`) plus either `markup` (cost-plus multiplier) or `fixed` + `unit`. Returns `rule_id`, `created`. Raises `VALIDATION_ERROR` if cost tracking is disabled or the scope/price is invalid.
