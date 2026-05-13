# Refreshing the STT and TTS Pricing Catalogs

VoiceGateway uses [pydantic/genai-prices](https://github.com/pydantic/genai-prices) for LLM pricing. STT and TTS pricing live in two local catalogs that the project maintains by hand:

- `src/voicegateway/pricing/stt.py`
- `src/voicegateway/pricing/tts.py`

Each entry carries a `pricing_source_date` (the date the maintainer last verified the rate against the provider's pricing page) and a `pricing_source_url`. The dates are per entry, so a refresh of one provider does not lie about the freshness of the others.

## When a refresh is required

`src/voicegateway/tests/pricing/test_staleness.py` runs in CI and fails the build when any catalog entry's `pricing_source_date` is more than 60 days older than `date.today()`. Either:

- A provider published a price change that affects an entry, or
- An entry crossed the 60-day staleness threshold.

In both cases, follow the steps below for the affected entries.

## Step-by-step refresh

1. Open the relevant catalog file (`src/voicegateway/pricing/stt.py` or `src/voicegateway/pricing/tts.py`).
2. Find the entry whose rate or date you are refreshing.
3. Visit the entry's `pricing_source_url` and read the current published rate. If the provider changed pricing tiers, plan caps, or unit conventions, capture both numbers (old and new) in the commit body so reviewers can verify the calculation.
4. Update both the rate (`per_minute` for STT, `per_character` for TTS) and the `pricing_source_date` to the date you actually pulled the page. Use a `date(YYYY, M, D)` literal:
   ```python
   "deepgram/nova-3": STTEntry(
       per_minute=Decimal("0.0043"),
       pricing_source_date=date(2026, 7, 12),  # was 2026, 5, 4
       pricing_source_url="https://deepgram.com/pricing",
   ),
   ```
5. Confirm the staleness gate passes locally:
   ```bash
   pytest src/voicegateway/tests/pricing/test_staleness.py -q
   ```
6. Confirm the entry-level tests still pass:
   ```bash
   pytest src/voicegateway/tests/pricing/ -q
   ```
7. Commit with a message that names the entry, the old rate, and the new rate:
   ```
   chore(pricing): refresh deepgram/nova-3 STT rate

   Deepgram's pricing page now lists Nova-3 streaming at $0.0046/min
   (was $0.0043). Bumped pricing_source_date to 2026-07-12.
   Source: https://deepgram.com/pricing
   ```

## Adding a new entry

When adding a new STT or TTS model:

1. Pick the catalog file that matches the modality.
2. Append a new entry, mirroring the shape of the existing ones. Use a `Decimal` literal for the rate; never a `float`.
3. Set `pricing_source_date` to the date you verified the rate.
4. Run the full pricing suite:
   ```bash
   pytest src/voicegateway/tests/pricing/ -q
   ```
5. If the new model has unusual billing semantics (credit systems, plan-tier dependent rates, audio-seconds vs characters mismatches), add a comment above the entry explaining the estimate and pointing readers at `voicegw reconcile` for verification.

## What the `PRICING_SOURCE` attribution string surfaces

Both catalogs derive a module-level `PRICING_SOURCE` string of the form `voicegateway-catalog@<oldest_date>`. The oldest per-entry date wins, so the attribution per request is honest about worst-case freshness. Refreshing a single entry without bumping the others moves the catalog's apparent freshness only when the refreshed entry was the oldest one.

## Why this is hand-maintained

LLM pricing has the [pydantic/genai-prices](https://github.com/pydantic/genai-prices) library publishing canonical rates with a release cadence VoiceGateway can pin against. STT and TTS have no equivalent project, so the catalog is maintained directly. The 60-day gate keeps the maintenance cost visible: if no one refreshes within two months, CI breaks until someone does.
