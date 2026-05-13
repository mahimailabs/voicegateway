# Phase 1: Inference path consolidation + reconcile fold

**Date:** 2026-05-13
**Branch:** `feat/struc-refactor`
**Status:** Approved
**Estimated blast radius:** ~60 import-site updates, one commit, low risk

---

## Goal

Reduce `src/voicegateway/`'s top-level package count from 15 to 11 by consolidating every part of the inference data flow under `inference/`, and folding the two-file `reconcile/` subpackage into `services/`.

After this commit the top-level layout reads as:

```
src/voicegateway/
├── cli/
├── core/
├── data/
├── inference/                ← absorbs providers, pricing, session
├── middleware/
├── models/
├── repository/
├── schemas/
├── server/
├── services/                 ← absorbs reconcile
├── storage/                  ← unchanged; decomposition is Phase 2
└── utils/
```

## Why these specific dirs

| Current top-level | Reason it belongs nested or folded |
|---|---|
| `providers/` | LiveKit plugin wrappers. Only consumed by `inference/_factory.py` and `core/registry.py`. Conceptually part of the inference path. |
| `pricing/` | Cost catalog keyed by modality (llm/stt/tts). Consumed by `middleware/cost_tracker.py` and the inference modules. Modality alignment matches inference. |
| `reconcile/` | Two files (`core.py` + a re-export `__init__.py`). Reconciliation arithmetic is service-shaped. |
| `inference/_*.py` | Every file uses a `_` filename prefix. Python's `_` convention applies to module-level *names*, not filenames. The public surface is `inference.STT(...)` via `__init__.py` re-exports; submodule filenames don't need the underscore. |

## Target layout

### `inference/` (after)

```
src/voicegateway/inference/
├── __init__.py                  # public re-exports: STT, LLM, TTS
├── factory.py                   # was _factory.py
├── stt.py                       # was _stt.py
├── llm.py                       # was _llm.py
├── tts.py                       # was _tts.py
├── project.py                   # was _project.py
├── resolution.py                # was _resolution.py
├── session/
│   ├── __init__.py              # re-exports common helpers
│   ├── attach.py                # was _session_attach.py
│   └── context.py               # was _session_context.py
├── providers/                   # was top-level providers/
│   ├── __init__.py
│   ├── base.py
│   ├── anthropic_provider.py
│   ├── assemblyai_provider.py
│   ├── cartesia_provider.py
│   ├── deepgram_provider.py
│   ├── elevenlabs_provider.py
│   ├── groq_provider.py
│   ├── kokoro_provider.py
│   ├── ollama_provider.py
│   ├── openai_provider.py
│   ├── piper_provider.py
│   └── whisper_provider.py
└── pricing/                     # was top-level pricing/
    ├── __init__.py
    ├── catalog.py
    ├── llm.py
    ├── stt.py
    └── tts.py
```

### `services/` (after)

Add one file: `services/reconciliation_service.py` (content moved from
`reconcile/core.py`). Delete `voicegateway/reconcile/` entirely.

## Public API contract

Unchanged at the entry point. The drop-in surface remains:

```python
from voicegateway import inference

stt = inference.STT("deepgram/nova-3")
llm = inference.LLM("openai/gpt-4o-mini")
tts = inference.TTS("cartesia/sonic-3")
```

The `inference/__init__.py` re-export list is the contract. The
`inference.session`, `inference.providers`, `inference.pricing`
subpackages are public but treated as implementation packages
(callers should prefer the top-level `inference.STT/LLM/TTS` API).

## Internal API changes (the import sweep)

Path rewrites (mechanical, via Python script):

| From | To |
|---|---|
| `voicegateway.inference._factory` | `voicegateway.inference.factory` |
| `voicegateway.inference._stt` | `voicegateway.inference.stt` |
| `voicegateway.inference._llm` | `voicegateway.inference.llm` |
| `voicegateway.inference._tts` | `voicegateway.inference.tts` |
| `voicegateway.inference._project` | `voicegateway.inference.project` |
| `voicegateway.inference._resolution` | `voicegateway.inference.resolution` |
| `voicegateway.inference._session_attach` | `voicegateway.inference.session.attach` |
| `voicegateway.inference._session_context` | `voicegateway.inference.session.context` |
| `voicegateway.providers.*` | `voicegateway.inference.providers.*` |
| `voicegateway.pricing.*` | `voicegateway.inference.pricing.*` |
| `voicegateway.reconcile.core` | `voicegateway.services.reconciliation_service` |
| `voicegateway.reconcile` (attribute access) | `voicegateway.services.reconciliation_service` |

Affected directories (callers): `cli/`, `core/`, `middleware/`, `server/`,
`schemas/`, `tests/`, `src/dashboard/`. Estimated ~60 import-site edits.

## Execution order

1. `git mv inference/_*.py inference/<name>.py` (drop underscore prefix; 8 files).
2. Create `inference/session/`, move `session_attach.py` and `session_context.py` under it.
3. `git mv providers/ inference/providers/`.
4. `git mv pricing/ inference/pricing/`.
5. `git mv reconcile/core.py services/reconciliation_service.py`. Delete `reconcile/__init__.py` and the directory.
6. Rewrite all import sites with one Python script (regex on `voicegateway.inference._x`, `voicegateway.providers`, `voicegateway.pricing`, `voicegateway.reconcile`).
7. Update `inference/__init__.py` to re-export from the new submodule paths.
8. Update `inference/session/__init__.py` to re-export the common session helpers (currently the most-used names from `_session_context`).
9. The subpackage count in `tests/integration/test_public_api.py` should net to 30 (unchanged): three top-level packages leave (`providers`, `pricing`, `reconcile`); three new nested packages appear (`inference.providers`, `inference.pricing`, `inference.session`). Verify empirically and update only if it diverges.
10. Run `ruff check --fix` and `ruff format`.
11. Run full `pytest -q`. Expect 1642 passed, 4 skipped.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Regex rewrite over-matches (e.g., string literals, prose) | Anchor patterns on `voicegateway.<X>` exactly. Diff before commit. |
| `inference/session/__init__.py` re-export list misses a name | Grep tests for `from voicegateway.inference._session_context import` and ensure every imported name is re-exported. |
| Subpackage-count contract test trips | Update the assertion to the actual measured count. |
| External docs reference `voicegateway.providers.X` | Phase 1 doesn't touch `docs/` content; user-facing API (`voicegateway.inference.STT`) is unchanged. |

## Out of scope

- `storage/sqlite.py` decomposition (covered by `2026-05-13-storage-decomposition-design.md`).
- Removal of the legacy function-based repositories in `repository/*_repository.py`.
- Auth path migration (`core/auth.py` continues to use `repository.virtual_keys_repository`).

## Verification checklist

- [ ] `python -c "from voicegateway import inference; print(inference.STT, inference.LLM, inference.TTS)"` resolves.
- [ ] `python -c "from voicegateway.inference.providers import deepgram_provider"` resolves.
- [ ] `python -c "from voicegateway.inference.pricing import catalog"` resolves.
- [ ] `python -c "from voicegateway.services.reconciliation_service import reconcile"` resolves.
- [ ] `python -c "import voicegateway.reconcile" → ImportError` (the old path is gone).
- [ ] `python -m pytest -q` reports 1642 passed, 4 skipped.
- [ ] `ruff check src/voicegateway src/dashboard` is clean.
- [ ] `voicegw reconcile --help` works end-to-end (the CLI command still runs).
