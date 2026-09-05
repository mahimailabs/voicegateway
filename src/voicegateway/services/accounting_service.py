"""Transactional immutable-pricing and usage-ledger operations."""

from __future__ import annotations

import hashlib
import json
import time
import uuid

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.accounting.contracts import (
    PricingRevisionCreate,
    PricingSide,
    RecordReceipt,
    UsageEnvelope,
)
from voicegateway.accounting.rating import rate_usage
from voicegateway.models.accounting_model import (
    AccountingProjection,
    AccountingUsage,
    PricingRevision,
)


class RevisionConflict(ValueError):
    pass


class AccountingService:
    def __init__(self, session: AsyncSession, *, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def create_revision(
        self, payload: PricingRevisionCreate
    ) -> tuple[PricingRevision, bool]:
        content = payload.canonical_content()
        digest = payload.content_hash()
        query = select(PricingRevision).where(
            PricingRevision.tenant_id == self._tenant_id,
            PricingRevision.side == payload.side.value,
            PricingRevision.revision_id == payload.revision_id,
        )
        existing = (await self._session.execute(query)).scalar_one_or_none()
        if existing is not None:
            if existing.content_hash != digest:
                raise RevisionConflict(
                    "revision identity already exists with different content"
                )
            return existing, False
        row = PricingRevision(
            tenant_id=self._tenant_id,
            revision_id=payload.revision_id,
            side=payload.side.value,
            scope_json=json.dumps(
                payload.scope.model_dump(mode="json"), sort_keys=True
            ),
            content_json=content,
            content_hash=digest,
            contract_version=payload.contract_version,
            currency=payload.currency,
            created_at_ns=time.time_ns(),
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row, True

    async def get_revision(
        self, side: PricingSide, revision_id: str
    ) -> PricingRevision | None:
        query = select(PricingRevision).where(
            PricingRevision.tenant_id == self._tenant_id,
            PricingRevision.side == side.value,
            PricingRevision.revision_id == revision_id,
        )
        return (await self._session.execute(query)).scalar_one_or_none()

    async def activate_revision(
        self, side: PricingSide, revision_id: str
    ) -> PricingRevision:
        row = await self.get_revision(side, revision_id)
        if row is None:
            raise LookupError("pricing revision not found")
        if hashlib.sha256(row.content_json.encode()).hexdigest() != row.content_hash:
            raise RevisionConflict("stored pricing revision hash mismatch")
        await self._session.execute(
            update(PricingRevision)
            .where(
                PricingRevision.tenant_id == self._tenant_id,
                PricingRevision.side == side.value,
            )
            .values(active=False)
        )
        row.active = True
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def ingest(self, envelope: UsageEnvelope) -> RecordReceipt:
        canonical = envelope.model_dump_json()
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        query = select(AccountingUsage).where(
            AccountingUsage.tenant_id == self._tenant_id,
            AccountingUsage.project_id == envelope.project_id,
            AccountingUsage.event_id == envelope.event_id,
        )
        existing = (await self._session.execute(query)).scalar_one_or_none()
        if existing is not None:
            if existing.payload_hash != digest:
                return RecordReceipt(
                    event_id=envelope.event_id,
                    outcome="rejected",
                    code="identity_conflict",
                )
            return RecordReceipt(
                event_id=envelope.event_id,
                outcome="duplicate",
                receipt_id=existing.receipt_id,
                code="already_committed",
            )

        acquisition_total, acquisition_complete = await self._rate(
            envelope, PricingSide.ACQUISITION
        )
        selling_total, selling_complete = await self._rate(
            envelope, PricingSide.SELLING
        )
        status = "rated" if acquisition_complete and selling_complete else "unrated"
        receipt_id = str(uuid.uuid4())
        row = AccountingUsage(
            tenant_id=self._tenant_id,
            project_id=envelope.project_id,
            event_id=envelope.event_id,
            attempt_id=envelope.attempt_id,
            component=envelope.component,
            modality=envelope.modality,
            offering=envelope.offering,
            model_id=envelope.model_id,
            session_id=envelope.session_id,
            turn_id=envelope.turn_id,
            producer_id=envelope.producer_id,
            ownership_mode=envelope.ownership_mode.value,
            acquisition_revision_id=envelope.acquisition_revision_id,
            selling_revision_id=envelope.selling_revision_id,
            occurred_at_ns=envelope.occurred_at_ns,
            payload_hash=digest,
            envelope_json=canonical,
            acquisition_total_usd=acquisition_total,
            selling_total_usd=selling_total,
            acquisition_complete=acquisition_complete,
            selling_complete=selling_complete,
            status=status,
            receipt_id=receipt_id,
            created_at_ns=time.time_ns(),
        )
        self._session.add(row)
        self._session.add(
            AccountingProjection(event_id=envelope.event_id, payload_json=canonical)
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            return RecordReceipt(
                event_id=envelope.event_id,
                outcome="rejected",
                code="attempt_or_ownership_conflict",
            )
        return RecordReceipt(
            event_id=envelope.event_id,
            outcome="accepted",
            receipt_id=receipt_id,
            code="committed",
        )

    async def _rate(
        self, envelope: UsageEnvelope, side: PricingSide
    ) -> tuple[str | None, bool]:
        revision_id = (
            envelope.acquisition_revision_id
            if side is PricingSide.ACQUISITION
            else envelope.selling_revision_id
        )
        if revision_id is None:
            return None, False
        row = await self.get_revision(side, revision_id)
        if (
            row is None
            or hashlib.sha256(row.content_json.encode()).hexdigest() != row.content_hash
        ):
            return None, False
        revision = PricingRevisionCreate.model_validate_json(row.content_json)
        return rate_usage(envelope, revision)

    async def report(self, *, project_id: str | None = None) -> dict[str, object]:
        conditions = [AccountingUsage.tenant_id == self._tenant_id]
        if project_id is not None:
            conditions.append(AccountingUsage.project_id == project_id)
        rows = (
            (
                await self._session.execute(
                    select(AccountingUsage).where(and_(*conditions))
                )
            )
            .scalars()
            .all()
        )
        selling = sum(
            (
                __import__("decimal").Decimal(row.selling_total_usd)
                for row in rows
                if row.selling_total_usd is not None
            ),
            start=__import__("decimal").Decimal(0),
        )
        counts = {
            name: sum(row.status == name for row in rows)
            for name in ("rated", "unrated", "incomplete", "rejected")
        }
        pending = (
            await self._session.execute(
                select(func.count())
                .select_from(AccountingProjection)
                .where(AccountingProjection.projected_at_ns.is_(None))
            )
        ).scalar_one()
        return {
            "selling_total_usd": format(selling, "f"),
            "counts": {**counts, "pending_delivery": pending},
            "records": len(rows),
        }
