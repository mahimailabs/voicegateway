"""Provider adapters: 11 implementations covering cloud and local models.

Each provider lives in its own submodule
(``voicegateway.providers.openai_provider``,
``voicegateway.providers.deepgram_provider``, etc.) and extends
``voicegateway.providers.base.BaseProvider``. The package re-exports
nothing at the top level; ``__all__`` is empty by design
(REQ-VG-POLISH-006 AC-1).
"""

__all__: list[str] = []
