# Sample provider usage exports

Reference fixtures for `voicegw reconcile`. Each file is a small,
plausible monthly usage export in the canonical schema documented at
`docs/reference/reconcile-formats.md`. Use them in two ways:

1. **As test inputs.** `tests/test_reconcile.py` and
   `tests/test_cli.py` load these directly to exercise the parser
   and `voicegw reconcile` end-to-end. Adding a new sample here
   should land alongside a focused test case that pins its
   parsed values.
2. **As documentation.** When `docs/reference/reconcile-formats.md`
   says "the canonical schema is `model,input_tokens,...`," these
   files are the authoritative example. Operators converting their
   raw provider exports into the canonical shape can diff their
   converter output against these files.

## Files

- `openai-sample.csv` — three LLM models (gpt-4o-mini, gpt-4o,
  gpt-4-turbo). Columns: model, input_tokens, output_tokens,
  n_requests, cost_usd. Units = input + output tokens.
- `deepgram-sample.csv` — three STT models (nova-3, nova-2,
  flux-general). Columns: model, audio_seconds, n_requests,
  cost_usd. Units = audio_seconds (NOT minutes — VG-side
  minutes-to-seconds conversion happens in `aggregate_vg_records`).
- `cartesia-sample.csv` — two TTS models (sonic-3, sonic-turbo).
  Columns: model, characters, credits, n_requests, cost_usd.
  Units = characters (TTS is character-billed; `credits` is
  Cartesia's internal billing unit and is intentionally NOT what
  the canonical reconcile schema reads).

## Schema invariants

- All cost fields are bare USD numbers (no currency prefix). Raw
  provider exports may use `$1.23` formatting; the operator strips
  those before reconcile (see reconcile-formats.md).
- One row per model. The parser overwrites on duplicate model
  names; aggregate first if the raw export carries per-day rows
  for the same model.
- Numeric units are floats or ints; the parser coerces both.
- Missing optional columns (n_requests, cost_usd) surface as 0.0
  rather than KeyError.
