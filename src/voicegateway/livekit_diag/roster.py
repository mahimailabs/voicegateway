"""Fetch the agent worker roster from the VoiceGateway collector.

The roster (idle / busy / offline registered workers) is what closes the gap the
LiveKit server API leaves. It is populated by each agent's ``register_worker``
heartbeat and read here with the same ``vk_`` key, tenant-scoped server-side.
Best-effort: any error (unset collector, network, auth) yields an empty roster so
``voicegw livekit agents`` still shows the in-room view.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def fetch_roster(collector_url: str, api_key: str | None) -> list[dict[str, Any]]:
    """GET ``{collector}/v1/agents``; return the roster, or [] on any error."""
    try:
        import httpx

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                collector_url.rstrip("/") + "/v1/agents", headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 - roster is a best-effort enrichment
        logger.debug("fetch_roster failed", exc_info=True)
        return []
