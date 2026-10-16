# Adding a Provider

VoiceGateway does not construct STT, LLM, or TTS instances for your agent. You build the native plugin yourself (`livekit.plugins.*` or `pipecat.services.*`) and pass it to [`attach()`](https://docs.voicegateway.dev/docs) or [`guard()`](https://docs.voicegateway.dev/docs). VoiceGateway meters that instance by its `model_id` string and prices it through [voice-prices](https://github.com/mahimailabs/voice-prices).

That means "adding a provider" almost always means one thing: making sure the `provider/model` id resolves to a price. There is no VoiceGateway provider class to write for this.

## Add or confirm a pricing entry
### Check whether the model already prices
```python
from voicegateway.inference.pricing import catalog

catalog.calculate_cost("stt", "<provider>/<model>", audio_seconds=60)
catalog.calculate_cost("llm", "<provider>/<model>", input_tokens=1000, output_tokens=500)
catalog.calculate_cost("tts", "<provider>/<model>", character_count=1000)
```
`None` means the model is not yet in `voice-prices`. Self-hosted ids (`local/*`, `ollama/*`) always return `Decimal('0')` and need no entry.
### Add the model to voice-prices
Add the model id, match pattern, and `prices` block in the relevant provider file under [voice-prices](https://github.com/mahimailabs/voice-prices)'s `prices/providers/`. Every entry carries a `prices_checked` date and a `pricing_source_url`. Publish a new `voice-prices` version.
### Bump the pin
Update the `voice-prices` dependency spec in VoiceGateway's `pyproject.toml` (currently `voice-prices>=0.11.0,<1`) to require the new version, then confirm it resolves:

```bash
pytest src/voicegateway/tests/pricing/ -q
```
See [Refreshing Pricing](refreshing-pricing.md) for the full workflow, including what to do when a provider changes an existing rate.

## Document it

If the provider is new to VoiceGateway (not just a new model on an existing provider), add it to the provider table in the repository `README.md`. The docs site names no providers on purpose: pricing coverage lives in [voice-prices](https://prices.mahimai.ca).

## Related pages

- [Refreshing Pricing](refreshing-pricing.md)
- [Testing](testing.md)
- [Development Setup](development-setup.md)
- [Contributing](index.md)
