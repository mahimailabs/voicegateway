"""Transactional immutable-pricing and usage-ledger operations."""

# mypy: disable-error-code="arg-type,attr-defined,union-attr"

from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from voicegateway.accounting.contracts import (
    OwnershipAssignment,
    OwnershipMode,
    PreparationRequest,
    PricingBinding,
    PricingRevisionCreate,
    PricingSide,
    RecordReceipt,
    UsageEnvelope,
)
from voicegateway.accounting.rating import rate_usage
from voicegateway.models.accounting_model import (
    AccountingOwnership,
    AccountingProjection,
    AccountingRejection,
    AccountingUsage,
    PreparedPricingBinding,
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
            scope_key=json.dumps(
                payload.scope.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
            content_json=content,
            content_hash=digest,
            contract_version=payload.contract_version,
            currency=payload.currency,
            created_at_ns=time.time_ns(),
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            concurrent = (await self._session.execute(query)).scalar_one()
            if concurrent.content_hash != digest:
                raise RevisionConflict(
                    "revision identity already exists with different content"
                ) from None
            return concurrent, False
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
        self,
        side: PricingSide,
        revision_id: str,
        *,
        expected_current_revision_id: str | None = None,
    ) -> PricingRevision:
        row = await self.get_revision(side, revision_id)
        if row is None:
            raise LookupError("pricing revision not found")
        if hashlib.sha256(row.content_json.encode()).hexdigest() != row.content_hash:
            raise RevisionConflict("stored pricing revision hash mismatch")
        current = (
            await self._session.execute(
                select(PricingRevision).where(
                    PricingRevision.tenant_id == self._tenant_id,
                    PricingRevision.side == side.value,
                    PricingRevision.scope_key == row.scope_key,
                    PricingRevision.active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if expected_current_revision_id is not None and (
            current is None or current.revision_id != expected_current_revision_id
        ):
            raise RevisionConflict("active revision changed")
        await self._session.execute(
            update(PricingRevision)
            .where(
                PricingRevision.tenant_id == self._tenant_id,
                PricingRevision.side == side.value,
                PricingRevision.scope_key == row.scope_key,
            )
            .values(active=False)
        )
        row.active = True
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def set_ownership(
        self, assignment: OwnershipAssignment
    ) -> AccountingOwnership:
        query = select(AccountingOwnership).where(
            AccountingOwnership.tenant_id == self._tenant_id,
            AccountingOwnership.project_id == assignment.project_id,
            AccountingOwnership.component == assignment.component,
        )
        row = (await self._session.execute(query)).scalar_one_or_none()
        if row is None:
            row = AccountingOwnership(
                tenant_id=self._tenant_id,
                project_id=assignment.project_id,
                component=assignment.component,
                mode=assignment.mode.value,
                updated_at_ns=time.time_ns(),
            )
        else:
            row.mode = assignment.mode.value
            row.updated_at_ns = time.time_ns()
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def prepare(self, request: PreparationRequest) -> PricingBinding:
        ownership_query = select(AccountingOwnership).where(
            AccountingOwnership.tenant_id == self._tenant_id,
            AccountingOwnership.project_id == request.project_id,
            AccountingOwnership.component == request.component,
        )
        ownership = (await self._session.execute(ownership_query)).scalar_one_or_none()
        mode = (
            OwnershipMode(ownership.mode)
            if ownership is not None
            else OwnershipMode.SDK
        )
        revisions: dict[PricingSide, str | None] = {}
        for side in PricingSide:
            rows = (
                (
                    await self._session.execute(
                        select(PricingRevision).where(
                            PricingRevision.tenant_id == self._tenant_id,
                            PricingRevision.side == side.value,
                            PricingRevision.active.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
            matches = []
            for row in rows:
                scope = json.loads(row.scope_json)
                if scope["offering"] != request.offering:
                    continue
                if scope.get("project_id") not in (None, request.project_id):
                    continue
                if scope.get("component") not in (None, request.component):
                    continue
                matches.append(
                    (
                        sum(
                            scope.get(key) is not None
                            for key in ("project_id", "component")
                        ),
                        row,
                    )
                )
            matches.sort(key=lambda item: item[0], reverse=True)
            revisions[side] = matches[0][1].revision_id if matches else None
        binding = PreparedPricingBinding(
            binding_id=str(uuid.uuid4()),
            tenant_id=self._tenant_id,
            project_id=request.project_id,
            component=request.component,
            offering=request.offering,
            acquisition_revision_id=revisions[PricingSide.ACQUISITION],
            selling_revision_id=revisions[PricingSide.SELLING],
            ownership_mode=mode.value,
            prepared_at_ns=time.time_ns(),
        )
        self._session.add(binding)
        await self._session.commit()
        return PricingBinding(
            binding_id=binding.binding_id,
            project_id=binding.project_id,
            component=binding.component,
            offering=binding.offering,
            acquisition_revision_id=binding.acquisition_revision_id,
            selling_revision_id=binding.selling_revision_id,
            ownership_mode=mode,
            prepared_at_ns=binding.prepared_at_ns,
        )

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
                return await self._reject(envelope, digest, "identity_conflict")
            return RecordReceipt(
                event_id=envelope.event_id,
                outcome="duplicate",
                receipt_id=existing.receipt_id,
                code="already_committed",
            )

        if envelope.pricing_binding_id is not None:
            binding = await self._session.get(
                PreparedPricingBinding, envelope.pricing_binding_id
            )
            if binding is None or binding.tenant_id != self._tenant_id:
                return await self._reject(envelope, digest, "binding_not_found")
            expected = (
                binding.project_id,
                binding.component,
                binding.offering,
                binding.ownership_mode,
                binding.acquisition_revision_id,
                binding.selling_revision_id,
            )
            supplied = (
                envelope.project_id,
                envelope.component,
                envelope.offering,
                envelope.ownership_mode.value,
                envelope.acquisition_revision_id,
                envelope.selling_revision_id,
            )
            if expected != supplied:
                return await self._reject(envelope, digest, "binding_mismatch")

        acquisition_total, acquisition_complete = await self._rate(
            envelope, PricingSide.ACQUISITION
        )
        selling_total, selling_complete = await self._rate(
            envelope, PricingSide.SELLING
        )
        has_missing_measurement = any(
            item.value is None for item in envelope.quantities
        )
        status = (
            "rated"
            if acquisition_complete and selling_complete
            else "incomplete"
            if has_missing_measurement
            else "unrated"
        )
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
            pricing_binding_id=envelope.pricing_binding_id,
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
            AccountingProjection(
                tenant_id=self._tenant_id,
                project_id=envelope.project_id,
                event_id=envelope.event_id,
                payload_json=canonical,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            concurrent = (await self._session.execute(query)).scalar_one_or_none()
            if concurrent is not None:
                if concurrent.payload_hash == digest:
                    return RecordReceipt(
                        event_id=envelope.event_id,
                        outcome="duplicate",
                        receipt_id=concurrent.receipt_id,
                        code="already_committed",
                    )
                return await self._reject(envelope, digest, "identity_conflict")
            return await self._reject(envelope, digest, "attempt_or_ownership_conflict")
        return RecordReceipt(
            event_id=envelope.event_id,
            outcome="accepted",
            receipt_id=receipt_id,
            code="committed",
        )

    async def _reject(
        self, envelope: UsageEnvelope, payload_hash: str, code: str
    ) -> RecordReceipt:
        query = select(AccountingRejection).where(
            AccountingRejection.tenant_id == self._tenant_id,
            AccountingRejection.project_id == envelope.project_id,
            AccountingRejection.event_id == envelope.event_id,
            AccountingRejection.payload_hash == payload_hash,
        )
        existing = (await self._session.execute(query)).scalar_one_or_none()
        if existing is not None:
            return RecordReceipt(
                event_id=envelope.event_id,
                outcome="rejected",
                receipt_id=existing.receipt_id,
                code=existing.code,
            )
        receipt_id = str(uuid.uuid4())
        self._session.add(
            AccountingRejection(
                tenant_id=self._tenant_id,
                project_id=envelope.project_id,
                event_id=envelope.event_id,
                payload_hash=payload_hash,
                code=code,
                receipt_id=receipt_id,
                created_at_ns=time.time_ns(),
            )
        )
        await self._session.commit()
        return RecordReceipt(
            event_id=envelope.event_id,
            outcome="rejected",
            receipt_id=receipt_id,
            code=code,
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

    async def report(
        self,
        *,
        project_id: str | None = None,
        allowed_project_ids: frozenset[str] | None = None,
        group_by: tuple[str, ...] = (),
        include_acquisition: bool = False,
    ) -> dict[str, object]:
        conditions = [AccountingUsage.tenant_id == self._tenant_id]
        if project_id is not None:
            conditions.append(AccountingUsage.project_id == project_id)
        elif allowed_project_ids is not None:
            conditions.append(AccountingUsage.project_id.in_(allowed_project_ids))
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
                Decimal(row.selling_total_usd)
                for row in rows
                if row.selling_total_usd is not None
            ),
            start=Decimal(0),
        )
        acquisition = sum(
            (
                Decimal(row.acquisition_total_usd)
                for row in rows
                if row.acquisition_total_usd is not None
            ),
            start=Decimal(0),
        )
        counts = {
            name: sum(row.status == name for row in rows)
            for name in ("rated", "unrated", "incomplete", "rejected")
        }
        projection_conditions = [AccountingProjection.tenant_id == self._tenant_id]
        rejection_conditions = [AccountingRejection.tenant_id == self._tenant_id]
        if project_id is not None:
            projection_conditions.append(AccountingProjection.project_id == project_id)
            rejection_conditions.append(AccountingRejection.project_id == project_id)
        elif allowed_project_ids is not None:
            projection_conditions.append(
                AccountingProjection.project_id.in_(allowed_project_ids)
            )
            rejection_conditions.append(
                AccountingRejection.project_id.in_(allowed_project_ids)
            )
        pending = (
            await self._session.execute(
                select(func.count())
                .select_from(AccountingProjection)
                .where(
                    and_(
                        *projection_conditions,
                        AccountingProjection.projected_at_ns.is_(None),
                    )
                )
            )
        ).scalar_one()
        counts["rejected"] = (
            await self._session.execute(
                select(func.count())
                .select_from(AccountingRejection)
                .where(and_(*rejection_conditions))
            )
        ).scalar_one()
        result: dict[str, object] = {
            "selling_total_usd": format(selling, "f"),
            "counts": {**counts, "pending_delivery": pending},
            "records": len(rows),
        }
        if include_acquisition:
            result["acquisition_total_usd"] = format(acquisition, "f")
            result["margin_usd"] = format(selling - acquisition, "f")
        allowed_groups = {
            "session": "session_id",
            "component": "component",
            "modality": "modality",
            "model": "model_id",
            "acquisition_revision": "acquisition_revision_id",
            "selling_revision": "selling_revision_id",
        }
        if any(item not in allowed_groups for item in group_by):
            raise ValueError("unsupported reconciliation group")
        groups: dict[tuple[str | None, ...], list[AccountingUsage]] = {}
        for row in rows:
            key = tuple(getattr(row, allowed_groups[item]) for item in group_by)
            groups.setdefault(key, []).append(row)
        result["groups"] = [
            {
                "keys": dict(zip(group_by, key, strict=True)),
                "records": len(group_rows),
                "selling_total_usd": format(
                    sum(
                        (
                            Decimal(row.selling_total_usd)
                            for row in group_rows
                            if row.selling_total_usd is not None
                        ),
                        start=Decimal(0),
                    ),
                    "f",
                ),
            }
            for key, group_rows in groups.items()
        ]
        return result
