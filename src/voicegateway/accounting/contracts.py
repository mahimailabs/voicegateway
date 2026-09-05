"""Versioned accounting wire contracts.

All monetary values are decimal strings.  These types deliberately reject
arbitrary metadata: accounting rows must never become a second transcript or
prompt store.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = 1
ROUNDING_PROFILE = "usd-v1-half-even-12"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PricingSide(StrEnum):
    ACQUISITION = "acquisition"
    SELLING = "selling"


class PricingDimension(StrEnum):
    TEXT_INPUT = "text_input"
    TEXT_OUTPUT = "text_output"
    CACHE_READ = "cache_read"
    CACHE_WRITE = "cache_write"
    REALTIME_AUDIO_INPUT = "realtime_audio_input"
    REALTIME_AUDIO_OUTPUT = "realtime_audio_output"
    REALTIME_AUDIO_CACHE = "realtime_audio_cache"
    CHARACTERS = "characters"
    AUDIO_SECONDS = "audio_seconds"
    REQUESTS = "requests"


class Unit(StrEnum):
    TOKEN = "token"
    CHARACTER = "character"
    SECOND = "second"
    REQUEST = "request"


class OwnershipMode(StrEnum):
    SDK = "sdk"
    EXTERNAL = "external"


class MeasurementStatus(StrEnum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


class RevisionScope(StrictModel):
    tenant_id: str | None = None
    project_id: str | None = None
    component: str | None = None
    offering: str

    @field_validator("tenant_id", "project_id", "component", "offering")
    @classmethod
    def valid_id(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            raise ValueError("invalid scope identifier")
        return value


_UNIT_BY_DIMENSION = {
    PricingDimension.TEXT_INPUT: Unit.TOKEN,
    PricingDimension.TEXT_OUTPUT: Unit.TOKEN,
    PricingDimension.CACHE_READ: Unit.TOKEN,
    PricingDimension.CACHE_WRITE: Unit.TOKEN,
    PricingDimension.REALTIME_AUDIO_INPUT: Unit.TOKEN,
    PricingDimension.REALTIME_AUDIO_OUTPUT: Unit.TOKEN,
    PricingDimension.REALTIME_AUDIO_CACHE: Unit.TOKEN,
    PricingDimension.CHARACTERS: Unit.CHARACTER,
    PricingDimension.AUDIO_SECONDS: Unit.SECOND,
    PricingDimension.REQUESTS: Unit.REQUEST,
}


def canonical_decimal(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal string") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("decimal value must be finite and non-negative")
    if abs(number.as_tuple().exponent) > 18:
        raise ValueError("decimal value has more than 18 fractional digits")
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


class DimensionRate(StrictModel):
    dimension: PricingDimension
    unit: Unit
    rate: str

    @field_validator("rate")
    @classmethod
    def valid_rate(cls, value: str) -> str:
        return canonical_decimal(value)

    @model_validator(mode="after")
    def fixed_unit(self) -> DimensionRate:
        if self.unit != _UNIT_BY_DIMENSION[self.dimension]:
            raise ValueError(
                f"{self.dimension} requires unit {_UNIT_BY_DIMENSION[self.dimension]}"
            )
        return self


class PricingRevisionCreate(StrictModel):
    contract_version: Literal[1] = CONTRACT_VERSION
    revision_id: str
    side: PricingSide
    scope: RevisionScope
    currency: Literal["USD"] = "USD"
    rounding_profile: Literal["usd-v1-half-even-12"] = ROUNDING_PROFILE
    rates: tuple[DimensionRate, ...]
    unsupported_dimensions: tuple[PricingDimension, ...] = ()

    @field_validator("revision_id")
    @classmethod
    def valid_revision_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("invalid revision_id")
        return value

    @model_validator(mode="after")
    def complete_dimensions(self) -> PricingRevisionCreate:
        rated = [item.dimension for item in self.rates]
        unsupported = list(self.unsupported_dimensions)
        if len(set(rated)) != len(rated) or len(set(unsupported)) != len(unsupported):
            raise ValueError("duplicate pricing dimension")
        if set(rated) & set(unsupported):
            raise ValueError("a dimension cannot be both rated and unsupported")
        if set(rated) | set(unsupported) != set(PricingDimension):
            raise ValueError("revision must classify every supported dimension")
        return self

    def canonical_content(self) -> str:
        raw = self.model_dump(mode="json")
        raw["rates"] = sorted(raw["rates"], key=lambda item: item["dimension"])
        raw["unsupported_dimensions"] = sorted(raw["unsupported_dimensions"])
        return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_content().encode()).hexdigest()


class Quantity(StrictModel):
    dimension: PricingDimension
    value: str | None = None
    status: MeasurementStatus

    @field_validator("value")
    @classmethod
    def valid_value(cls, value: str | None) -> str | None:
        return canonical_decimal(value) if value is not None else None

    @model_validator(mode="after")
    def status_matches_value(self) -> Quantity:
        present = self.status in {
            MeasurementStatus.MEASURED,
            MeasurementStatus.ESTIMATED,
        }
        if present != (self.value is not None):
            raise ValueError(
                "measured/estimated quantities require a value; missing/unsupported forbid one"
            )
        return self


class UsageEnvelope(StrictModel):
    contract_version: Literal[1] = CONTRACT_VERSION
    event_id: str
    attempt_id: str
    project_id: str
    session_id: str
    turn_id: str | None = None
    component: str
    modality: str
    offering: str
    model_id: str
    producer_id: str
    ownership_mode: OwnershipMode
    acquisition_revision_id: str | None = None
    selling_revision_id: str | None = None
    occurred_at_ns: Annotated[int, Field(ge=0)]
    quantities: tuple[Quantity, ...]

    @field_validator(
        "event_id",
        "attempt_id",
        "project_id",
        "session_id",
        "turn_id",
        "component",
        "modality",
        "offering",
        "model_id",
        "producer_id",
        "acquisition_revision_id",
        "selling_revision_id",
    )
    @classmethod
    def valid_ids(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            raise ValueError("invalid identifier")
        return value

    @model_validator(mode="after")
    def unique_quantities_and_cache_subsets(self) -> UsageEnvelope:
        dimensions = [item.dimension for item in self.quantities]
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("duplicate quantity dimension")
        values = {
            item.dimension: Decimal(item.value)
            for item in self.quantities
            if item.value is not None
        }
        cached = values.get(PricingDimension.CACHE_READ, Decimal(0)) + values.get(
            PricingDimension.CACHE_WRITE, Decimal(0)
        )
        if cached > values.get(PricingDimension.TEXT_INPUT, Decimal(0)):
            raise ValueError("cache quantities must be subsets of text_input")
        return self


class AccountingCapabilities(StrictModel):
    contract_version: Literal[1] = CONTRACT_VERSION
    dimensions: tuple[PricingDimension, ...] = tuple(PricingDimension)
    units: tuple[Unit, ...] = tuple(Unit)
    currencies: tuple[Literal["USD"], ...] = ("USD",)
    rate_fractional_digits: int = 18
    total_fractional_digits: int = 12
    rounding_profile: str = ROUNDING_PROFILE
    ownership_modes: tuple[OwnershipMode, ...] = tuple(OwnershipMode)


class RecordReceipt(StrictModel):
    event_id: str
    outcome: Literal["accepted", "duplicate", "rejected", "retryable"]
    receipt_id: str | None = None
    code: str


class UsageBatchResponse(StrictModel):
    receipts: tuple[RecordReceipt, ...]
