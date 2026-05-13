from datetime import date, datetime
from typing import Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field, PrivateAttr
from src.util.schema import make_optional

ConditionType = Union["LogicalCondition", "FieldOperatorCondition"]


class LogicalCondition(BaseModel):
    operator: Literal["AND", "OR"]
    conditions: list["ConditionType"]


class FilterSchema(BaseModel):
    operator: Literal["AND", "OR"]
    conditions: list[ConditionType]


class ModelBaseInfo(BaseModel):
    id: int
    uuid: UUID
    created_at: datetime
    updated_at: datetime


class DraftModelBaseInfo(ModelBaseInfo):
    is_draft: bool = True


class FieldOperatorCondition(BaseModel):
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
    value: Any


class SortOrder(BaseModel):
    field: str
    direction: Literal["asc", "desc"]


class FindBase(BaseModel):
    ordering: str | None = None
    sort_orders: list[SortOrder] | None = None
    page: int | None = 1
    page_size: int | None = 10
    search: str | None = None
    filters: FilterSchema | None = None
    searchable_fields: list[str] | None = None


class SearchOptions(FindBase):
    total_count: int | None = None
    total_pages: int | None = None


class FindResult(BaseModel):
    founds: list | None = None
    search_options: SearchOptions | None = None


class DateRangeBase(BaseModel):
    from_date: date = Field(
        date.today().replace(day=1),
        description="Start date",
    )
    to_date: date = Field(
        date.today(),
        description="End date",
    )
    _date_column: str | None = PrivateAttr(default="created_at")


class SearchOptions(FindBase):
    total_count: int | None


class FindDateRange(BaseModel):
    created_at__lt: str
    created_at__lte: str
    created_at__gt: str
    created_at__gte: str


class Blank(BaseModel):
    pass


class FindUniqueValues(make_optional(FindBase)):
    field_name: str


class UniqueValuesResult(BaseModel):
    founds: list[Any]
    search_options: SearchOptions | None
