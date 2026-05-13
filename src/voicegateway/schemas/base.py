"""Shared request/response pydantic shapes for list-and-filter endpoints.

``FindBase`` is the standard pagination + ordering + search payload.
``FilterSchema`` is the nested AND/OR + per-field-operator language
generic list endpoints accept. ``ModelBaseInfo`` is the slim envelope
every detail response embeds (id + uuid + timestamps).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field


class FieldOperatorCondition(BaseModel):
    """Single ``field op value`` filter rule."""

    field: str
    operator: Literal[
        "eq",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "like",
        "ilike",
        "between",
        "is_null",
        "is_not_null",
    ]
    value: Any = None


class LogicalCondition(BaseModel):
    """Recursive AND/OR group of filter rules."""

    operator: Literal["AND", "OR"]
    conditions: list[ConditionType]


ConditionType = Union[LogicalCondition, FieldOperatorCondition]
LogicalCondition.model_rebuild()


class FilterSchema(BaseModel):
    """Top-level filter envelope passed to repository ``read_by_options``."""

    operator: Literal["AND", "OR"] = "AND"
    conditions: list[ConditionType] = Field(default_factory=list)


class SortOrder(BaseModel):
    """One ``field ASC|DESC`` sort instruction."""

    field: str
    direction: Literal["asc", "desc"] = "asc"


class FindBase(BaseModel):
    """Standard pagination + ordering + search envelope."""

    ordering: str | None = None
    sort_orders: list[SortOrder] | None = None
    page: int | None = 1
    page_size: int | None = 20
    search: str | None = None
    filters: FilterSchema | None = None


class SearchOptions(FindBase):
    """``FindBase`` plus the totals every list response echoes back."""

    total_count: int | None = None
    total_pages: int | None = None


class FindResult(BaseModel):
    """Generic list-endpoint envelope: rows + pagination metadata."""

    founds: list[Any] | None = None
    search_options: SearchOptions | None = None


class ModelBaseInfo(BaseModel):
    """The id + audit fields every detail response embeds."""

    id: int
    uuid: UUID
    created_at: datetime
    updated_at: datetime
