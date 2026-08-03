"""``voicegw calls``: per-call cost/activity view."""

from __future__ import annotations

import asyncio
import re

import yaml
from typer.testing import CliRunner

from voicegateway.cli._app import app
from voicegateway.cli.calls_cli import _fmt_started
from voicegateway.core.gateway import Gateway
from voicegateway.models.request_model import RequestRecord

runner = CliRunner()


def _strip(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_fmt_started_short_and_passthrough() -> None:
    assert _fmt_started("2026-07-23T03:52:46.871326+00:00") == "07-23 03:52"
    assert _fmt_started("") == "-"
    assert _fmt_started(None) == "-"
    assert _fmt_started("not-a-date") == "not-a-date"  # unparseable -> first 16 chars


def _rec(
    session_id: str, modality: str, model_id: str, provider: str, cost: float, ts: float
) -> RequestRecord:
    return RequestRecord(
        id=f"{session_id}-{modality}-{ts}",
        timestamp=ts,
        project="default",
        modality=modality,
        model_id=model_id,
        provider=provider,
        input_units=1,
        output_units=0,
        cost_usd=cost,
        pricing_source="test",
        ttfb_ms=None,
        total_latency_ms=100.0,
        status="success",
        session_id=session_id,
    )


def _seed(tmp_path, monkeypatch) -> str:
    db = tmp_path / "calls.db"
    monkeypatch.setenv("VOICEGW_DB_PATH", str(db))
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "x"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "cost_tracking": {"enabled": True},
            }
        )
    )
    gw = Gateway(config_path=str(cfg))

    async def seed() -> None:
        await gw.storage._ensure_initialized()
        turn = [
            ("stt", "deepgram/nova-3", "deepgram", 0.001),
            ("llm", "openai/gpt-4o", "openai", 0.020),
            ("tts", "cartesia/sonic-3", "cartesia", 0.010),
        ]
        for i, (mod, model, prov, cost) in enumerate(turn):
            await gw.storage.log_request(
                _rec("call-001", mod, model, prov, cost, 1_700_000 + i)
            )

    asyncio.run(seed())
    return str(cfg)


def test_calls_lists_one_row_per_call(tmp_path, monkeypatch) -> None:
    cfg = _seed(tmp_path, monkeypatch)
    result = runner.invoke(app, ["calls", "-c", cfg])
    assert result.exit_code == 0, result.output
    out = _strip(result.output)
    assert "call-001" in out
    assert "Recent Calls" in out
    assert "1 calls" in out  # three requests collapse into ONE call row
    assert "$0.0310" in out  # 0.001 + 0.020 + 0.010 total for the call


def test_calls_rejects_unknown_sort(tmp_path, monkeypatch) -> None:
    cfg = _seed(tmp_path, monkeypatch)
    result = runner.invoke(app, ["calls", "-c", cfg, "--sort", "bogus"])
    assert result.exit_code == 2
    assert "Unknown sort" in _strip(result.output)
