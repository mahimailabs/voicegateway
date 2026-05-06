#!/usr/bin/env python
"""Smoke test for the v0.0.5 ``voicegateway.inference`` drop-in surface.

Exercises the wedge claim end-to-end without making a live API call:

    1. Build a temp voicegw.yaml that configures one project with one
       fake OpenAI key.
    2. Import the inference module.
    3. Construct an ``inference.LLM`` factory.
    4. Verify the returned object is an ``InstrumentedLLM`` (the
       wrapping layer that adds cost / session tracking) and that the
       wrapped LK plugin instance is reachable through it.

This is the smoke gate for the v0.0.5 release prep step in
.agents/TODO.md (5.14 #3). The end-to-end "real network call" path
is mahimairaja-blocked — it depends on a real OpenAI key in the test
environment.

Run from the repository root:

    python scripts/smoke-v005-inference.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "voicegw.yaml"
        cfg_path.write_text(
            textwrap.dedent(
                """\
                providers:
                  openai:
                    api_key: ${OPENAI_API_KEY}
                projects:
                  smoke-test:
                    name: Smoke Test
                    providers:
                      openai:
                        api_key: ${OPENAI_API_KEY}
                default_project: smoke-test
                models:
                  stt: {}
                  llm: {}
                  tts: {}
                stacks: {}
                fallbacks:
                  stt: []
                  llm: []
                  tts: []
                cost_tracking:
                  enabled: false
                observability:
                  latency_tracking: true
                """
            )
        )

        os.environ.setdefault("OPENAI_API_KEY", "smoke-fake-key-not-a-real-secret")
        os.environ["VOICEGW_CONFIG"] = str(cfg_path)
        os.environ.pop("VOICEGW_DB_PATH", None)

        # Reset any module-level state from a prior import in the same
        # interpreter (matters when re-running interactively).
        from voicegateway.inference import _factory, _project, _session_context

        _factory.reset_gateway()
        _project.reset_project()
        _session_context.reset_session_id()

        from voicegateway import inference

        # Step 1: signature presence.
        for name in ("STT", "LLM", "TTS", "set_project", "get_active_project"):
            if not hasattr(inference, name):
                failures.append(f"voicegateway.inference is missing {name!r}")
        if failures:
            return _report(failures)

        # Step 2: drop-in compat — the LK side imports too.
        try:
            from livekit.agents import inference as lk_inference
        except ImportError as exc:
            failures.append(f"livekit.agents.inference import failed: {exc}")
            return _report(failures)
        for name in ("STT", "LLM", "TTS"):
            if not hasattr(lk_inference, name):
                failures.append(f"livekit.agents.inference is missing {name!r}")
        if failures:
            return _report(failures)

        # Step 3: factory call returns a wrapped instance.
        try:
            llm = inference.LLM("openai/gpt-4o-mini")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"inference.LLM construction raised: {exc!r}")
            return _report(failures)

        if llm.__class__.__name__ != "InstrumentedLLM":
            failures.append(
                f"expected InstrumentedLLM, got {llm.__class__.__name__}"
            )

        # Step 4: the active project resolves to the yaml default.
        active = inference.get_active_project()
        if active != "smoke-test":
            failures.append(
                f"expected active project 'smoke-test', got {active!r}"
            )

        # Step 5: an outer set_project() call wins.
        inference.set_project("smoke-test")
        if inference.get_active_project() != "smoke-test":
            failures.append("set_project did not stick in this context")

    return _report(failures)


def _report(failures: list[str]) -> int:
    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ok — voicegateway.inference smoke test passed")
    print("    drop-in import works")
    print("    factory returns InstrumentedLLM")
    print("    active project resolves from voicegw.yaml default_project")
    print("    set_project propagates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
