---
title: Admin MCP Tools
description: The fifteen VoiceGateway MCP tools hidden unless the server starts with VOICEGW_MCP_ADMIN=1. Provider config, per-project keys, and every destructive delete.
---

Set `VOICEGW_MCP_ADMIN=1` on the process running `voicegw mcp` to expose these. See [Default tools](/mcp/tools) for the ten visible without it.

Every `delete_*` tool below uses the same two-phase confirmation: call with `confirm=false` (the default) to get a `CONFIRMATION_REQUIRED` error carrying an impact preview, then call again with `confirm=true`.

## Observability

### get_provider_status

Args: `provider_id` (optional). Reads provider config from `voicegw.yaml` only, no network call. Returns, per provider, `configured` (has credentials or is a local type), `type` (`"cloud"` | `"local"`), `model_count`, `has_api_key`. If `provider_id` is set and unknown, returns `{providers: {}, missing: [provider_id]}`. For a live connectivity check, use `test_provider` below.

## Models

### register_model

Args: `modality`, `provider_id`, `model_name`, plus optional `display_name`, `default_language`, `default_voice`, `config`. The generated id is `{provider_id}/{model_name}`. Raises `PROVIDER_NOT_FOUND` (provider not yet added), `MODEL_ALREADY_EXISTS`, or `VALIDATION_ERROR` (cost tracking disabled).

### delete_model

Args: `model_id`, `confirm`. Only deletes DB-registered models (`register_model`); a YAML-defined model raises `READ_ONLY_RESOURCE`. Preview and confirmed responses both carry `projects_affected` (YAML projects whose `default_stack` references the model).

## Projects

### delete_project

Args: `project_id`, `confirm`. Only deletes DB-created projects (`create_project`); a YAML-defined project raises `READ_ONLY_RESOURCE`. Does not delete that project's request logs. Preview and confirmed responses carry `total_spend_usd`, `total_requests`, `last_activity`.

## Rate card

### delete_rate_card_override

Args: `rule_id` (from `get_rate_card`'s `overrides`). Raises `VALIDATION_ERROR` if the id doesn't exist or cost tracking is disabled.

## Providers

These ten tools store and test provider credentials. They're legacy: VoiceGateway no longer constructs a provider class to route a request, so `add_provider` does not change what `attach()`/`guard()` do for your agent. What still runs in production is `health_check()`, reached here through `test_provider` and `vg_test_provider_key` (the same call `voicegw doctor` and `POST /v1/providers/{id}/test` make).

### Top level: `list_providers`, `get_provider`, `test_provider`, `add_provider`, `delete_provider`

Scoped by a single `provider_id`. `add_provider` takes `provider_id`, `provider_type` (one of `deepgram`, `openai`, `anthropic`, `groq`, `cartesia`, `elevenlabs`, `assemblyai`, `ollama`, `whisper`, `kokoro`, `piper`), `api_key`, `base_url`; for cloud types it runs `health_check()` before saving and raises `PROVIDER_TEST_FAILED` on failure. Every returned API key is masked (`sk-a...1f2b`), never returned in full. `list_providers` takes no arguments; `get_provider` and `test_provider` take `provider_id` and raise `PROVIDER_NOT_FOUND` if unknown; `delete_provider` needs `confirm` and only removes DB-added providers (a YAML one raises `READ_ONLY_RESOURCE`), returning `models_affected`/`projects_affected` in its preview.

### Per project: `vg_add_provider`, `vg_remove_provider`, `vg_list_providers`, `vg_set_provider_key`, `vg_test_provider_key`

Scoped by `project` + `provider` together, stored under a composite id `"<project>:<provider>"` so two projects can each hold their own key for the same provider type without colliding. `vg_add_provider` creates the row (`project`, `provider`, `api_key`, optional `base_url`). `vg_set_provider_key` rotates an existing one and raises `PROVIDER_NOT_FOUND` if the pair doesn't exist yet (use `vg_add_provider` first). `vg_remove_provider` deletes it. `vg_list_providers` takes an optional `project` filter and returns YAML-defined and DB-managed keys together. `vg_test_provider_key` resolves the key (checking the YAML `projects.<project>.providers.<provider>` block first, then the DB row) and runs `health_check()` against it. Every tool in this group raises `READ_ONLY_RESOURCE` if the matching row is YAML-defined; API keys are Fernet-encrypted at rest and always returned masked.
