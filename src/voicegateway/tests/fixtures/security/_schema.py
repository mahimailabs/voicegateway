"""Typed shape for the Wave 0 cross-tenant security fixtures.

A fixture is one request made by one actor against one victim tenant, plus
what the contract says must happen. Three kinds exist, and the ``kind`` field
selects which other fields are required:

``guarantee``
    Production already behaves correctly. ``contract`` alone is required, and
    the runner asserts it as an ordinary passing test. These exist so the
    format is not only a list of defects: a guarantee that is never asserted
    is a guarantee that can regress silently.

``characterization``
    Production deviates. Both ``contract`` and ``observed`` are required and
    they must differ, which is enforced here rather than left to review. The
    runner asserts ``observed`` (documenting today) and separately asserts
    ``contract`` under a strict xfail (the Wave 1 target).

``absence``
    No surface exists to enforce the rule yet. ``absent_surfaces`` is
    required, and the runner asserts those attributes genuinely do not exist,
    so "planned" is falsifiable rather than a promise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voicegateway.schemas.telemetry.security_schema import GapId


class Actor(BaseModel):
    """Who makes the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: ``None`` together with ``role="none"`` means no credential at all.
    tenant_id: str | None = None
    role: Literal["tenant", "admin", "none"]


class RequestSpec(BaseModel):
    """The request to replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["GET", "POST", "PATCH", "PUT", "DELETE"]
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    #: A list where the endpoint takes a bare array (the ingest routes do).
    json_body: dict | list | None = None


class Expectation(BaseModel):
    """What the response must look like."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Accepts a set so a contract can say "any 4xx refusal" without pinning
    #: an implementation choice Wave 1 has not made yet.
    status_code: list[int] = Field(min_length=1)
    #: Rows attributable to the victim tenant that the actor caused to be
    #: written, or was able to read. ``None`` where the case does not count.
    victim_rows: int | None = None
    note: str = ""


class AbsentSurface(BaseModel):
    """An attribute that must not exist yet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module: str
    attribute: str
    #: Field or column that must be absent from ``attribute``.
    field: str


class SecurityFixture(BaseModel):
    """One cross-tenant security case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    kind: Literal["guarantee", "characterization", "absence"]
    #: ``None`` only for ``guarantee`` cases, which record no defect.
    gap_id: GapId | None = None
    #: Why this case exists and what it proves. Read by a human, not asserted.
    rationale: str
    victim_tenant_id: str | None = None
    actor: Actor
    request: RequestSpec | None = None
    contract: Expectation | None = None
    observed: Expectation | None = None
    absent_surfaces: list[AbsentSurface] = Field(default_factory=list)

    @model_validator(mode="after")
    def _required_fields_follow_kind(self) -> SecurityFixture:
        """Each kind must carry exactly the evidence its runner needs."""
        if self.kind == "guarantee":
            if self.gap_id is not None:
                raise ValueError(
                    f"{self.case_id}: a guarantee records no defect, so it "
                    "carries no gap_id"
                )
            if self.observed is not None:
                raise ValueError(
                    f"{self.case_id}: a guarantee's observed behavior is its "
                    "contract; drop the observed block"
                )
            if self.request is None or self.contract is None:
                raise ValueError(
                    f"{self.case_id}: a guarantee needs a request and a contract"
                )
            return self

        if self.gap_id is None:
            raise ValueError(f"{self.case_id}: kind={self.kind} requires a gap_id")

        if self.kind == "characterization":
            if self.request is None or self.contract is None:
                raise ValueError(
                    f"{self.case_id}: a characterization needs a request and a contract"
                )
            if self.observed is None:
                raise ValueError(
                    f"{self.case_id}: a characterization must record what "
                    "production does today"
                )
            if self.observed == self.contract:
                raise ValueError(
                    f"{self.case_id}: observed equals contract, so this is a "
                    "guarantee, not a characterization"
                )
            return self

        if not self.absent_surfaces:
            raise ValueError(
                f"{self.case_id}: an absence case must name what is absent"
            )
        return self


__all__ = [
    "AbsentSurface",
    "Actor",
    "Expectation",
    "RequestSpec",
    "SecurityFixture",
]
