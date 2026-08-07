---
title: How VoiceGateway compares
description: Where VoiceGateway fits against LiteLLM, OpenRouter, Cloudflare AI Gateway, and LiveKit Inference, and the cases where one of those is the better answer.
---

VoiceGateway profiles voice agents and the infrastructure they run on. Most tools
it gets compared to are LLM proxies, which solve a different problem: they sit in
the request path and route text completions. VoiceGateway sits beside the path and
measures it, across three pricing units (audio-minutes, tokens, characters) and
three stack layers.

## Pick by what you are building

| If you are... | Use |
|---|---|
| Building a LiveKit or Pipecat voice agent and want per-modality cost tracking | VoiceGateway |
| Profiling a LiveKit SFU or SIP path you operate | VoiceGateway |
| Self-hosting voice with local and cloud models in one view | VoiceGateway |
| Building a text-only LLM app (chatbot, RAG, code generation) | [LiteLLM](https://docs.litellm.ai/) |
| Wanting a hosted multi-tenant LLM proxy with no infrastructure | [OpenRouter](https://openrouter.ai/) |
| At production scale on Cloudflare and wanting a gateway in that stack | [Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/) |
| On managed LiveKit Cloud and happy with bundled inference pricing | LiveKit Inference |

## The structural difference

An LLM proxy owns the request. Your code calls the proxy, the proxy calls the
provider, and you inherit a network hop plus a single point of failure.

VoiceGateway owns nothing. Your agent keeps its native provider plugins and its
own API keys. [`attach()`](/guide/attach) subscribes to metric events the
framework already emits, so there is no proxy hop and no added latency on
happy-path calls. [`guard()`](/guide/guard) is opt-in per provider when you want
fallback or limits.

That choice has a cost worth stating: VoiceGateway cannot meter a provider your
framework does not emit metrics for, and it cannot rewrite a request in flight.

## What VoiceGateway does not do

- It is not an inference router. It does not pick models for you or load-balance across providers.
- It does not manage or proxy your provider API keys.
- It does not generate call load. The SIP path imports evidence from a load generator you run yourself.
- It does not provision infrastructure. The SFU and SIP layers profile a LiveKit deployment you already operate.

## Related

- [Which layer do you need?](/guide/decision-tree): route by the layer you are profiling.
- [What is VoiceGateway](/guide/what-is-voicegateway): the problem statement and the two-seam model.
- [FAQ](/reference/faq): shorter answers to the questions that come up most.
