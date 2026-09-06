"""Executable immutable-pricing synchronization and pinned-usage example.

Set ``VOICEGW_URL``, ``VOICEGW_OPERATOR_KEY``, and ``VOICEGW_TENANT`` and run:

    python examples/accounting_sync.py

The example uses synthetic identifiers and prints no credentials or price
content.  It requires an operator key because acquisition revisions are
operator-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class SyncResult:
    side: str
    revision_id: str
    synchronized: bool
    detail: str


def canonical_hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def revision_payload(
    revision_id: str, side: str, tenant: str, request_rate: str
) -> dict[str, Any]:
    return {
        "contract_version": 1,
        "revision_id": revision_id,
        "side": side,
        "scope": {"tenant_id": tenant, "offering": "provider/model"},
        "currency": "USD",
        "rounding_profile": "usd-v1-half-even-12",
        "rates": [{"dimension": "requests", "unit": "request", "rate": request_rate}],
        "unsupported_dimensions": [
            "audio_seconds",
            "cache_read",
            "cache_write",
            "characters",
            "realtime_audio_cache",
            "realtime_audio_input",
            "realtime_audio_output",
            "text_input",
            "text_output",
        ],
    }


async def synchronize_revision(
    client: httpx.AsyncClient,
    *,
    tenant: str,
    payload: dict[str, Any],
    expected_current_revision_id: str | None = None,
) -> SyncResult:
    """Create, read back, verify, then activate one pricing side independently."""
    side = str(payload["side"])
    revision_id = str(payload["revision_id"])
    created = await client.post(
        "/v1/accounting/revisions", params={"tenant": tenant}, json=payload
    )
    if created.status_code not in {200, 201}:
        return SyncResult(
            side, revision_id, False, f"create_http_{created.status_code}"
        )

    readback = await client.get(
        f"/v1/accounting/revisions/{side}/{revision_id}",
        params={"tenant": tenant},
    )
    if readback.status_code != 200:
        return SyncResult(
            side, revision_id, False, f"readback_http_{readback.status_code}"
        )
    stored = readback.json()
    content = stored.get("content")
    if not isinstance(content, dict) or canonical_hash(content) != stored.get(
        "content_hash"
    ):
        return SyncResult(side, revision_id, False, "readback_hash_mismatch")

    capabilities = (await client.get("/v1/accounting/capabilities")).json()
    classified = {rate["dimension"] for rate in content["rates"]} | set(
        content["unsupported_dimensions"]
    )
    if classified != set(capabilities["dimensions"]):
        return SyncResult(side, revision_id, False, "dimension_set_incomplete")

    activation: dict[str, Any] = {"revision_id": revision_id}
    if expected_current_revision_id is not None:
        activation["expected_current_revision_id"] = expected_current_revision_id
    activated = await client.post(
        f"/v1/accounting/revisions/{side}/activate",
        params={"tenant": tenant},
        json=activation,
    )
    if activated.status_code != 200:
        return SyncResult(
            side, revision_id, False, f"activate_http_{activated.status_code}"
        )
    return SyncResult(side, revision_id, True, "verified_and_active")


async def prepare_binding(client: httpx.AsyncClient, *, project: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/accounting/prepare",
        json={
            "project_id": project,
            "component": "conversation",
            "offering": "provider/model",
        },
    )
    response.raise_for_status()
    return response.json()


async def ingest_pinned_usage(
    client: httpx.AsyncClient,
    *,
    project: str,
    binding: dict[str, Any],
) -> dict[str, Any]:
    attempt_id = str(uuid.uuid4())
    response = await client.post(
        "/v1/accounting/usage",
        json=[
            {
                "contract_version": 1,
                "event_id": str(uuid.uuid4()),
                "attempt_id": attempt_id,
                "project_id": project,
                "session_id": "example-session",
                "turn_id": "example-turn",
                "component": "conversation",
                "modality": "llm",
                "offering": "provider/model",
                "model_id": "provider/model",
                "producer_id": "example-producer",
                "ownership_mode": binding["ownership_mode"],
                "pricing_binding_id": binding["binding_id"],
                "selling_revision_id": binding["selling_revision_id"],
                "occurred_at_ns": time.time_ns(),
                "quantities": [
                    {
                        "dimension": "requests",
                        "value": "1",
                        "status": "measured",
                    }
                ],
            }
        ],
    )
    response.raise_for_status()
    receipt = response.json()["receipts"][0]
    if receipt["outcome"] not in {"accepted", "duplicate"} or not receipt.get(
        "receipt_id"
    ):
        raise RuntimeError(f"usage was not durably acknowledged: {receipt['code']}")
    return receipt


async def run(base_url: str, operator_key: str, tenant: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {operator_key}"}
    project = "example-project"
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), headers=headers, timeout=10
    ) as client:
        first_results = await asyncio.gather(
            synchronize_revision(
                client,
                tenant=tenant,
                payload=revision_payload(
                    "example-acquisition-v1", "acquisition", tenant, "0.01"
                ),
            ),
            synchronize_revision(
                client,
                tenant=tenant,
                payload=revision_payload(
                    "example-selling-v1", "selling", tenant, "0.02"
                ),
            ),
        )
        binding = await prepare_binding(client, project=project)

        # Activate a new selling revision after preparation. The delayed event
        # below still uses the immutable v1 binding and therefore the v1 price.
        second = await synchronize_revision(
            client,
            tenant=tenant,
            payload=revision_payload("example-selling-v2", "selling", tenant, "0.03"),
            expected_current_revision_id="example-selling-v1",
        )
        receipt = await ingest_pinned_usage(client, project=project, binding=binding)
        return {
            "synchronization": [*first_results, second],
            "receipt": receipt,
            "pinned_selling_revision_id": binding["selling_revision_id"],
        }


async def _main() -> None:
    base_url = os.environ.get("VOICEGW_URL", "http://127.0.0.1:8080")
    operator_key = os.environ.get("VOICEGW_OPERATOR_KEY", "")
    tenant = os.environ.get("VOICEGW_TENANT", "")
    if not operator_key or not tenant:
        raise SystemExit("VOICEGW_OPERATOR_KEY and VOICEGW_TENANT are required")
    result = await run(base_url, operator_key, tenant)
    print(
        json.dumps(
            {
                "synchronization": [
                    item.__dict__ for item in result["synchronization"]
                ],
                "receipt_outcome": result["receipt"]["outcome"],
                "pinned_selling_revision_id": result["pinned_selling_revision_id"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
