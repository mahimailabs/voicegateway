"""Executable boundaries for the supported accounting release."""

from __future__ import annotations

import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.server import build_app
from voicegateway.utils.cli.export_costs import _EXPORT_COLUMNS


class _ForbiddenClickHouse:
    def __getattr__(self, name: str):
        raise AssertionError(f"exact accounting unexpectedly used ClickHouse: {name}")


async def test_supported_accounting_reads_are_sql_only_and_expose_no_pending_metric(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "voicegw.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "providers": {},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "cost_tracking": {"enabled": True},
            }
        )
    )
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "accounting.db"))
    gateway = Gateway(config_path=str(config))
    app = build_app(gateway)
    app.state.ch_client = _ForbiddenClickHouse()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in (
            "/v1/accounting/capabilities",
            "/v1/accounting/report",
            "/api/accounting",
        ):
            response = await client.get(path)
            assert response.status_code == 200, response.text
            assert "pending_delivery" not in response.text

    await gateway.storage.aclose()


def test_legacy_cost_export_cannot_expose_exact_accounting_cost_basis() -> None:
    """The only current export is legacy request-cost export, not ledger export."""
    assert not {
        "acquisition_revision_id",
        "acquisition_total_usd",
        "margin_usd",
    }.intersection(_EXPORT_COLUMNS)
