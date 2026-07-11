"""Service over billing_repository: rated-usage rollups per tenant/period."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voicegateway.repository import billing_repository as repo

if TYPE_CHECKING:
    from voicegateway.core.database import Database


class BillingService:
    """Rolled-up billable usage (rated revenue, cost, margin) per tenant."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def get_billable_usage(
        self,
        period: str = "month",
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
        tenant: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return one billing rollup row per tenant for the window."""
        async with self._db.session() as s:
            return await repo.get_billable_usage(
                s,
                period=period,
                start_ts=start_ts,
                end_ts=end_ts,
                project=project,
                tenant=tenant,
            )

    async def get_tenant_line_items(
        self,
        tenant: str,
        period: str = "month",
        start_ts: float | None = None,
        end_ts: float | None = None,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the per-(modality, model) invoice detail for one tenant."""
        async with self._db.session() as s:
            return await repo.get_tenant_line_items(
                s,
                tenant=tenant,
                period=period,
                start_ts=start_ts,
                end_ts=end_ts,
                project=project,
            )


__all__ = ["BillingService"]
