"""The published accounting synchronization example stays executable."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml
from httpx import ASGITransport, AsyncClient

from voicegateway.core.gateway import Gateway
from voicegateway.repository.api_keys_repository import create_api_key
from voicegateway.server import build_app

_EXAMPLE_PATH = Path(__file__).parents[4] / "examples" / "accounting_sync.py"
_SPEC = importlib.util.spec_from_file_location("accounting_sync_example", _EXAMPLE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_EXAMPLE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _EXAMPLE
_SPEC.loader.exec_module(_EXAMPLE)
ingest_pinned_usage = _EXAMPLE.ingest_pinned_usage
prepare_binding = _EXAMPLE.prepare_binding
revision_payload = _EXAMPLE.revision_payload
synchronize_revision = _EXAMPLE.synchronize_revision


async def test_sync_readback_and_delayed_pinned_usage_example(
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
                "auth": {"enforcement": "enforce"},
            }
        )
    )
    monkeypatch.delenv("VOICEGW_DB_URL", raising=False)
    monkeypatch.setenv("VOICEGW_DB_PATH", str(tmp_path / "example.db"))
    gateway = Gateway(config_path=str(config))
    async with gateway.storage.session() as session:
        key = await create_api_key(
            session,
            name="example-operator",
            role="admin",
            scopes="read,ingest,admin",
            tenant_id="example-tenant",
        )
    client = AsyncClient(
        transport=ASGITransport(app=build_app(gateway)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {key.plaintext}"},
    )
    async with client:
        acquisition = await synchronize_revision(
            client,
            tenant="example-tenant",
            payload=revision_payload(
                "example-acquisition-v1",
                "acquisition",
                "example-tenant",
                "0.01",
            ),
        )
        selling = await synchronize_revision(
            client,
            tenant="example-tenant",
            payload=revision_payload(
                "example-selling-v1", "selling", "example-tenant", "0.02"
            ),
        )
        assert acquisition.synchronized and selling.synchronized

        binding = await prepare_binding(client, project="example-project")
        switched = await synchronize_revision(
            client,
            tenant="example-tenant",
            payload=revision_payload(
                "example-selling-v2", "selling", "example-tenant", "0.03"
            ),
            expected_current_revision_id="example-selling-v1",
        )
        assert switched.synchronized
        receipt = await ingest_pinned_usage(
            client, project="example-project", binding=binding
        )
        assert receipt["outcome"] == "accepted"
        report = await client.get(
            "/v1/accounting/report?include_acquisition=true",
        )
        assert report.status_code == 200
        assert report.json()["selling_total_usd"] == "0.020000000000"
        assert report.json()["acquisition_total_usd"] == "0.010000000000"

    await gateway.storage.aclose()
