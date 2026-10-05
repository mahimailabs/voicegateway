# Inside your call

A dependency-free custom element for a visitor's own call measurements.
`voicegw serve` exposes the module at `/inside-call/widget.js`. It can also be
copied into the host application's public assets, as Mahimai's playground does.
The widget makes no requests and needs no collector credentials.

```html
<script type="module" src="/inside-call/widget.js"></script>
<voicegateway-inside-call></voicegateway-inside-call>
```

After `customElements.whenDefined('voicegateway-inside-call')`, assign the
host's authorized response to the element's `summary` property. It expects
`status` (`live` or `ended`), `elapsed_seconds`, `stale`, and `services` from
`voicegateway.services.inside_call.InsideCall.snapshot()`.

Create one `InsideCall` per call. Feed it metering records through `record()`;
its snapshot deduplicates record IDs and excludes transcripts and metadata.
The host must authenticate the viewer and check call ownership before returning
any summary. Keep the operator dashboard and collector API private.

STT input units are audio seconds, LLM units are input/output tokens, and TTS
input units are characters. Costs are estimated integer micro-USD. Timing is
mean provider first-token/first-audio latency, not end-to-end response time.
Missing timings remain absent. Hosting and transport costs are excluded.

The element inherits the host's font and supports `--color-ink`, `--color-muted`,
`--color-accent`, `--font-heading`, and `--font-mono` tokens. Its content uses
text nodes, never HTML supplied by a provider.
