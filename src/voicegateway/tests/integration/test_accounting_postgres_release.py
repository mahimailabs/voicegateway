"""Release acceptance for exact accounting on a disposable PostgreSQL.

CI supplies a fresh PostgreSQL service through ``VOICEGW_DB_URL``.  The tests
create a non-owner application role after migrations and exercise production
operations through that role.  They never target a persistent or shared DB.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from voicegateway.accounting.contracts import (
    DimensionRate,
    PreparationRequest,
    PricingDimension,
    PricingRevisionCreate,
    PricingSide,
    Quantity,
    RevisionScope,
    Unit,
    UsageEnvelope,
)
from voicegateway.accounting.outbox import AccountingOutbox
from voicegateway.core.gateway import Gateway
from voicegateway.models.accounting_model import AccountingUsage, PricingRevision
from voicegateway.repository.api_keys_repository import create_api_key
from voicegateway.server import build_app
from voicegateway.services.accounting_service import AccountingService, RevisionConflict

pytestmark = pytest.mark.skipif(
    not os.environ.get("VOICEGW_DB_URL", "").startswith("postgresql"),
    reason="requires a disposable PostgreSQL in VOICEGW_DB_URL",
)

_APP_ROLE = "voicegw_accounting_test_app"
_APP_PASSWORD = "voicegw_accounting_test_only"


def _revision(
    revision_id: str,
    side: PricingSide,
    *,
    tenant: str,
    rate: str,
) -> PricingRevisionCreate:
    return PricingRevisionCreate(
        revision_id=revision_id,
        side=side,
        scope=RevisionScope(tenant_id=tenant, offering="provider/model"),
        rates=(
            DimensionRate(
                dimension=PricingDimension.TEXT_INPUT,
                unit=Unit.TOKEN,
                rate=rate,
            ),
            DimensionRate(
                dimension=PricingDimension.TEXT_OUTPUT,
                unit=Unit.TOKEN,
                rate="0.000000000003",
            ),
            DimensionRate(
                dimension=PricingDimension.CACHE_READ,
                unit=Unit.TOKEN,
                rate="0.0000000000005",
            ),
        ),
        unsupported_dimensions=tuple(
            dimension
            for dimension in PricingDimension
            if dimension
            not in {
                PricingDimension.TEXT_INPUT,
                PricingDimension.TEXT_OUTPUT,
                PricingDimension.CACHE_READ,
            }
        ),
    )


def _usage(
    *,
    event_id: str,
    attempt_id: str,
    project: str,
    selling_revision_id: str,
    binding_id: str | None = None,
) -> UsageEnvelope:
    return UsageEnvelope(
        event_id=event_id,
        attempt_id=attempt_id,
        project_id=project,
        session_id="session-release-test",
        turn_id="turn-1",
        component="conversation",
        modality="llm",
        offering="provider/model",
        model_id="provider/model",
        producer_id="sdk-release-test",
        ownership_mode="sdk",
        pricing_binding_id=binding_id,
        selling_revision_id=selling_revision_id,
        occurred_at_ns=1_800_000_000_000_000_000,
        quantities=(
            Quantity(dimension="text_input", value="1000003", status="measured"),
            Quantity(dimension="text_output", value="7", status="measured"),
            Quantity(dimension="cache_read", value="3", status="measured"),
        ),
    )


async def _provision_restricted_role(owner_url: str) -> str:
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    DO $$ BEGIN
                      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                        CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{_APP_PASSWORD}';
                      END IF;
                    END $$;
                    """
                )
            )
            await connection.execute(
                text(f"ALTER ROLE {_APP_ROLE} PASSWORD '{_APP_PASSWORD}'")
            )
            await connection.execute(
                text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
            )
            await connection.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}"
                )
            )
            await connection.execute(
                text(
                    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"
                )
            )
    finally:
        await engine.dispose()
    parsed = make_url(owner_url).set(username=_APP_ROLE, password=_APP_PASSWORD)
    return parsed.render_as_string(hide_password=False)


def _config(tmp_path) -> str:
    path = tmp_path / "accounting-release.yaml"
    path.write_text(
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
    return str(path)


async def _activate(
    gateway: Gateway,
    tenant: str,
    side: PricingSide,
    revision_id: str,
    expected: str,
) -> str:
    try:
        async with gateway.storage.session() as session:
            await AccountingService(session, tenant_id=tenant).activate_revision(
                side, revision_id, expected_current_revision_id=expected
            )
    except RevisionConflict:
        return "conflict"
    return "activated"


class _ASGICollector:
    def __init__(self, client: AsyncClient, *, lose_first_ack: bool = False) -> None:
        self._client = client
        self._lose_first_ack = lose_first_ack
        self._calls = 0

    async def post(self, url: str, **kwargs):
        self._calls += 1
        response = await self._client.post(
            "/v1/accounting/usage",
            headers=kwargs.get("headers"),
            json=kwargs["json"],
        )
        if self._lose_first_ack and self._calls == 1:
            assert response.status_code == 200
            assert response.json()["receipts"][0]["outcome"] == "accepted"
            raise ConnectionError("synthetic acknowledgement loss after commit")
        return response


async def test_accounting_release_matrix_on_restricted_postgres(
    tmp_path, monkeypatch
) -> None:
    owner_url = os.environ["VOICEGW_DB_URL"]

    # The owner performs the additive migration.  Runtime operations below use
    # a role with no schema-create, delete, or drop privileges.
    monkeypatch.setenv("VOICEGW_DB_URL", owner_url)
    owner_gateway = Gateway(config_path=_config(tmp_path))
    async with owner_gateway.storage.session() as session:
        version = (
            await session.execute(
                text("SELECT version_num FROM alembic_version_voicegateway")
            )
        ).scalar_one()
        assert version == "a6c9e2f4b817"

    app_url = await _provision_restricted_role(owner_url)
    monkeypatch.setenv("VOICEGW_DB_URL", app_url)
    gateway = Gateway(config_path=_config(tmp_path))
    tenant = f"tenant-{uuid.uuid4().hex}"
    project = f"project-{uuid.uuid4().hex}"

    async with gateway.storage.session() as session:
        privileges = (
            await session.execute(
                text(
                    "SELECT current_user, "
                    "has_schema_privilege(current_user, 'public', 'CREATE'), "
                    "has_table_privilege(current_user, 'accounting_usage', 'DELETE')"
                )
            )
        ).one()
        assert privileges == (_APP_ROLE, False, False)

        service = AccountingService(session, tenant_id=tenant)
        acquisition_v1 = _revision(
            "acquisition-v1", PricingSide.ACQUISITION, tenant=tenant, rate="0.000001"
        )
        selling_v1 = _revision(
            "selling-v1", PricingSide.SELLING, tenant=tenant, rate="0.000002"
        )
        _, created = await service.create_revision(acquisition_v1)
        assert created
        _, created = await service.create_revision(selling_v1)
        assert created
        _, created = await service.create_revision(selling_v1)
        assert not created
        with pytest.raises(RevisionConflict):
            await service.create_revision(
                _revision("selling-v1", PricingSide.SELLING, tenant=tenant, rate="0.9")
            )
        await service.activate_revision(PricingSide.ACQUISITION, "acquisition-v1")
        await service.activate_revision(PricingSide.SELLING, "selling-v1")
        prepared = await service.prepare(
            PreparationRequest(
                project_id=project,
                component="conversation",
                offering="provider/model",
            )
        )

        for revision_id, side, rate in (
            ("selling-v2", PricingSide.SELLING, "0.000004"),
            ("selling-v3", PricingSide.SELLING, "0.000005"),
            ("acquisition-v2", PricingSide.ACQUISITION, "0.000009"),
        ):
            await service.create_revision(
                _revision(revision_id, side, tenant=tenant, rate=rate)
            )

    # Compare-and-swap activation is serialized by PostgreSQL. Exactly one
    # writer wins and an independent acquisition revision remains unchanged.
    outcomes = await asyncio.gather(
        _activate(gateway, tenant, PricingSide.SELLING, "selling-v2", "selling-v1"),
        _activate(gateway, tenant, PricingSide.SELLING, "selling-v3", "selling-v1"),
    )
    assert sorted(outcomes) == ["activated", "conflict"]
    async with gateway.storage.session() as session:
        active = (
            (
                await session.execute(
                    select(PricingRevision).where(
                        PricingRevision.tenant_id == tenant,
                        PricingRevision.active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sum(row.side == "selling" for row in active) == 1
        assert [row.revision_id for row in active if row.side == "acquisition"] == [
            "acquisition-v1"
        ]

    delayed = _usage(
        event_id=f"event-{uuid.uuid4().hex}",
        attempt_id=f"attempt-{uuid.uuid4().hex}",
        project=project,
        selling_revision_id="selling-v3",  # ignored in favor of the old binding
        binding_id=prepared.binding_id,
    )

    async def ingest_once() -> str:
        async with gateway.storage.session() as session:
            receipt = await AccountingService(session, tenant_id=tenant).ingest(delayed)
            return receipt.outcome

    duplicate_outcomes = await asyncio.gather(*(ingest_once() for _ in range(6)))
    assert duplicate_outcomes.count("accepted") == 1
    assert duplicate_outcomes.count("duplicate") == 5
    async with gateway.storage.session() as session:
        rows = (
            await session.execute(
                select(func.count())
                .select_from(AccountingUsage)
                .where(
                    AccountingUsage.tenant_id == tenant,
                    AccountingUsage.event_id == delayed.event_id,
                )
            )
        ).scalar_one()
        assert rows == 1
        report = await AccountingService(session, tenant_id=tenant).report(
            project_id=project, include_acquisition=True
        )
        # Old pinned rates: (1,000,000 uncached * 0.000002) + cache + output.
        assert Decimal(str(report["selling_total_usd"])) == Decimal("2.000000000022")
        assert Decimal(str(report["acquisition_total_usd"])) == Decimal(
            "1.000000000022"
        )

    # HTTP mixed batches return durable per-record receipts. Authentication,
    # not submitted tenant data, supplies the ledger tenant.
    async with gateway.storage.session() as session:
        key = await create_api_key(
            session,
            name="release-producer",
            scopes="read,ingest",
            tenant_id=tenant,
            project_ids=project,
        )
    app = build_app(gateway)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": f"Bearer {key.plaintext}"}
        allowed = _usage(
            event_id=f"event-{uuid.uuid4().hex}",
            attempt_id=f"attempt-{uuid.uuid4().hex}",
            project=project,
            selling_revision_id="selling-v1",
            binding_id=prepared.binding_id,
        ).model_dump(mode="json")
        denied = {
            **allowed,
            "event_id": f"event-{uuid.uuid4().hex}",
            "attempt_id": f"attempt-{uuid.uuid4().hex}",
            "project_id": "not-authorized",
        }
        mixed = await client.post(
            "/v1/accounting/usage", headers=headers, json=[allowed, denied]
        )
        assert mixed.status_code == 200
        assert [item["outcome"] for item in mixed.json()["receipts"]] == [
            "accepted",
            "rejected",
        ]
        assert mixed.json()["receipts"][1]["code"] == "project_not_authorized"

        tenant_report = await client.get("/v1/accounting/report", headers=headers)
        dashboard = await client.get("/api/accounting", headers=headers)
        denied_cost = await client.get(
            "/v1/accounting/report?include_acquisition=true", headers=headers
        )
        denied_revision = await client.get(
            "/v1/accounting/revisions/acquisition/acquisition-v1", headers=headers
        )
        assert tenant_report.status_code == dashboard.status_code == 200
        assert denied_cost.status_code == denied_revision.status_code == 403
        for response in (tenant_report, dashboard, denied_cost, denied_revision):
            assert "acquisition_total" not in response.text
            assert "margin_usd" not in response.text
            assert "0.000001" not in response.text

        # The collector commits, the acknowledgement is lost, and a restarted
        # sender retries the same event. PostgreSQL keeps one row and one charge.
        ack_event = _usage(
            event_id=f"event-{uuid.uuid4().hex}",
            attempt_id=f"attempt-{uuid.uuid4().hex}",
            project=project,
            selling_revision_id="selling-v1",
            binding_id=prepared.binding_id,
        )
        outbox_path = tmp_path / "postgres-lost-ack.db"
        first = AccountingOutbox(
            outbox_path,
            "http://test",
            api_key=key.plaintext,
            client=_ASGICollector(client, lose_first_ack=True),
        )
        assert await first.submit(ack_event) == "stored"
        assert (await first.drain())["retryable"] == 1
        await first.aclose()
        restarted = AccountingOutbox(
            outbox_path,
            "http://test",
            api_key=key.plaintext,
            client=_ASGICollector(client),
        )
        assert (await restarted.drain())["duplicate"] == 1
        assert (await restarted.health())["pending"] == 0
        await restarted.aclose()

    async with gateway.storage.session() as session:
        durable = (
            await session.execute(
                select(func.count())
                .select_from(AccountingUsage)
                .where(
                    AccountingUsage.tenant_id == tenant,
                    AccountingUsage.event_id == ack_event.event_id,
                )
            )
        ).scalar_one()
        assert durable == 1

    await gateway.storage.aclose()
    await owner_gateway.storage.aclose()
