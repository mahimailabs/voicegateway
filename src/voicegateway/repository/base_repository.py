from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import StrEnum
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlmodel import and_, asc, desc, distinct, func, or_, select
from src.core.config import config
from src.core.exceptions import DuplicatedError, NotFoundError, ValidationError
from src.models.base_model import BaseModel
from src.schemas.base_schema import SortOrder
from src.util.query_builder import dict_to_sqlalchemy_filter_options, get_field

T = TypeVar("T", bound=BaseModel)

DEFAULT_ORDERING: str = "asc"
DEFAULT_PAGE: int = 1
DEFAULT_PAGE_SIZE: int = 20


class FilterOperator(StrEnum):
    AND = "and"
    OR = "or"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ComparisonOperator(StrEnum):
    EQ = "eq"  # equals
    NE = "neq"  # not equals
    GT = "gt"  # greater than
    GTE = "gte"  # greater than or equal
    LT = "lt"  # less than
    LTE = "lte"  # less than or equal
    LIKE = "like"  # like
    ILIKE = "ilike"  # case-insensitive like
    IN = "in"  # in list
    NOT_IN = "not_in"  # not in list
    BETWEEN = "between"  # between two values
    IS_NULL = "is_null"  # is null
    IS_NOT_NULL = "is_not_null"  # is not null


class BaseRepository:
    def __init__(
        self,
        session_factory: Callable[..., AbstractAsyncContextManager[AsyncSession]],
        model: type[T],
    ) -> None:
        self.session_factory = session_factory
        self.model = model

    @asynccontextmanager
    async def _session(
        self, external: AsyncSession | None
    ) -> AsyncIterator[AsyncSession]:
        """Use an externally-provided session, or open a new one."""
        if external is not None:
            yield external
            return
        async with self.session_factory() as s:
            yield s

    def _apply_comparison_operator(
        self, field: Any, operator: ComparisonOperator, value: Any
    ) -> Any:
        if operator == ComparisonOperator.EQ:
            return field == value
        elif operator == ComparisonOperator.NE:
            return field != value
        elif operator == ComparisonOperator.GT:
            return field > value
        elif operator == ComparisonOperator.GTE:
            return field >= value
        elif operator == ComparisonOperator.LT:
            return field < value
        elif operator == ComparisonOperator.LTE:
            return field <= value
        elif operator == ComparisonOperator.LIKE:
            return field.like(f"%{value}%")
        elif operator == ComparisonOperator.ILIKE:
            return field.ilike(f"%{value}%")
        elif operator == ComparisonOperator.IN:
            return field.in_(value if isinstance(value, list) else [value])
        elif operator == ComparisonOperator.NOT_IN:
            return ~field.in_(value if isinstance(value, list) else [value])
        elif operator == ComparisonOperator.BETWEEN:
            if not isinstance(value, list) or len(value) != 2:
                raise ValidationError(
                    detail="BETWEEN operator requires a list with exactly 2 values"
                )
            return field.between(value[0], value[1])
        elif operator == ComparisonOperator.IS_NULL:
            return field.is_(None)
        elif operator == ComparisonOperator.IS_NOT_NULL:
            return field.is_not(None)
        else:
            raise ValidationError(detail=f"Unsupported comparison operator: {operator}")

    async def read_by_options(
        self,
        schema: T,
        searchable_fields: list[str] | None = None,
        eager: bool = False,
    ) -> dict:
        async with self.session_factory() as session:
            try:
                schema_as_dict: dict = schema.model_dump(exclude_none=True)
                if isinstance(searchable_fields, str):
                    searchable_fields = [searchable_fields]

                if searchable_fields is None and schema_as_dict.get("search"):
                    searchable_fields = ["name"]

                ordering: str = schema_as_dict.get("ordering", config.ORDERING)

                # Setup base query
                query = select(self.model)

                # Sorting
                if ordering.startswith("-"):
                    query = query.order_by(getattr(self.model, ordering[1:]).desc())
                else:
                    query = query.order_by(getattr(self.model, ordering).asc())

                page = schema_as_dict.get("page", config.PAGE)
                page_size = schema_as_dict.get("page_size", config.PAGE_SIZE)
                search_term = schema_as_dict.get("search")

                if eager:
                    for relation_path in getattr(self.model, "eagers", []):
                        path_parts = relation_path.split(".")
                        current_class = self.model
                        current_attr = getattr(current_class, path_parts[0])
                        loader = joinedload(current_attr)

                        for part in path_parts[1:]:
                            current_class = current_attr.property.mapper.class_
                            current_attr = getattr(current_class, part)
                            loader = loader.joinedload(current_attr)

                        query = query.options(loader)

                filter_dict = schema.model_dump(exclude_none=True)

                # Handle complex filters
                filters = filter_dict.get("filters", {})
                if filters:
                    query, filter_conditions = self._build_filter_conditions(
                        filters, query
                    )
                    if filter_conditions:
                        query = query.where(and_(*filter_conditions))

                if "search" in filter_dict:
                    del filter_dict["search"]

                # Apply standard filters
                filter_options = dict_to_sqlalchemy_filter_options(
                    self.model, filter_dict
                )
                query = query.where(filter_options)

                if searchable_fields and search_term:
                    search_filters = []
                    for field in searchable_fields:
                        if hasattr(self.model, field):
                            search_filters.append(
                                getattr(self.model, field).ilike(f"%{search_term}%")
                            )
                    if search_filters:
                        query = query.where(or_(*search_filters))

                from_date = getattr(schema, "from_date", None)
                to_date = getattr(schema, "to_date", None)
                _date_column = getattr(schema, "_date_column", "created_at")
                if from_date and to_date:
                    query = query.where(
                        getattr(self.model, _date_column) >= from_date,
                        getattr(self.model, _date_column) <= to_date,
                    )

                count_result = await session.execute(
                    select(func.count()).select_from(query.subquery())
                )
                total_count = count_result.scalar()

                if page_size == "all":
                    results_exec = await session.execute(query)
                    results = results_exec.scalars().all()
                else:
                    results_exec = await session.execute(
                        query.limit(page_size).offset((page - 1) * page_size)
                    )
                    results = results_exec.scalars().all()

                return {
                    "founds": results,
                    "search_options": {
                        "page": page,
                        "page_size": page_size,
                        "ordering": ordering,
                        "total_count": total_count,
                    },
                }
            except sa_exc.SQLAlchemyError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    def _build_filter_conditions(self, filter_dict: dict, query: Any) -> Any:
        conditions = []

        if not isinstance(filter_dict, dict):
            return query, conditions

        if "operator" in filter_dict and "conditions" in filter_dict:
            nested_conditions = []
            try:
                current_operator = FilterOperator(filter_dict["operator"].lower())
            except ValueError as e:
                raise ValidationError(
                    detail=f"Invalid operator: {filter_dict['operator']}. Must be one of: {', '.join(op.value for op in FilterOperator)}"
                ) from e

            # Process each condition in the list
            for condition in filter_dict["conditions"]:
                if isinstance(condition, dict):
                    # Handle nested operator/conditions
                    if "operator" in condition and "conditions" in condition:
                        query, nested_result = self._build_filter_conditions(
                            condition, query
                        )
                        if nested_result:
                            nested_conditions.extend(nested_result)

                    # Handle comparison operators
                    elif "field" in condition and "operator" in condition:
                        # is_null and is_not_null don't require value in condition
                        operator_str = condition["operator"].lower()
                        if operator_str in ("is_null", "is_not_null"):
                            value = condition.get("value", None)
                        elif "value" in condition:
                            value = condition["value"]
                        else:
                            raise ValidationError(
                                detail=f"Condition with operator '{condition['operator']}' requires a 'value' field"
                            )

                        try:
                            # Convert comparison operator to lowercase before creating enum
                            comparison_op = ComparisonOperator(operator_str)
                            query, field_name = get_field(
                                self.model, condition["field"], query
                            )
                            nested_conditions.append(
                                self._apply_comparison_operator(
                                    field_name, comparison_op, value
                                )
                            )
                        except ValueError as e:
                            raise ValidationError(
                                detail=f"Invalid comparison operator: {condition['operator']}. Must be one of: {', '.join(op.value for op in ComparisonOperator)}"
                            ) from e
                    else:
                        raise ValidationError(detail=f"Invalid condition: {condition}")

            if nested_conditions:
                conditions.append(
                    and_(*nested_conditions)
                    if current_operator == FilterOperator.AND
                    else or_(*nested_conditions)
                )

        return query, conditions

    def _build_sort_orders(self, sort_orders: list[SortOrder], query: Any) -> Any:
        orders = []
        for sort in sort_orders:
            field = sort.get("field")
            direction = sort.get("direction", SortDirection.ASC)

            if "." in field:
                query, column = get_field(self.model, field, query)
            else:
                if hasattr(self.model, field):
                    column = getattr(self.model, field)
                else:
                    raise ValidationError(
                        detail=f"Field '{field}' does not exist on model {self.model.__name__}"
                    )

            # Append the appropriate ordering direction
            if direction == SortDirection.DESC:
                orders.append(desc(column))
            else:
                orders.append(asc(column))

        return query, orders

    async def read_by_id(self, id: int, eager: bool = False):
        async with self.session_factory() as session:
            try:
                query = select(self.model).where(self.model.id == id)
                if eager:
                    for relation_path in getattr(self.model, "eagers", []):
                        path_parts = relation_path.split(".")
                        current_class = self.model
                        current_attr = getattr(current_class, path_parts[0])
                        loader = joinedload(current_attr)

                        for part in path_parts[1:]:
                            current_class = current_attr.property.mapper.class_
                            current_attr = getattr(current_class, part)
                            loader = loader.joinedload(current_attr)

                        query = query.options(loader)

                result_exec = await session.execute(query)
                result = result_exec.scalars().first()
                if not result:
                    raise NotFoundError(
                        detail=f"{self.model.__tablename__.capitalize()} with id {id} not found."
                    )
                return result
            except sa_exc.SQLAlchemyError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def create(self, schema: T):
        async with self.session_factory() as session:
            db_obj = self.model.model_validate(schema)

            try:
                db_obj = self.model(**schema.model_dump())
                session.add(db_obj)
                await session.commit()
                await session.refresh(db_obj)
                return db_obj
            except DuplicatedError:
                raise
            except sa_exc.IntegrityError as e:
                raise DuplicatedError(detail=str(e.orig)) from e
            except sa_exc.SQLAlchemyError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def update(self, id: int, schema: T):
        async with self.session_factory() as session:
            try:
                db_obj = await session.get(self.model, id)
                if not db_obj:
                    raise HTTPException(
                        status_code=404,
                        detail=f"{self.model.__tablename__.capitalize()} with id {id} not found.",
                    )

                obj_data = schema.model_dump(exclude_none=True)
                for key, value in obj_data.items():
                    setattr(db_obj, key, value)

                session.add(db_obj)
                await session.commit()
                await session.refresh(db_obj)

                return db_obj

            except sa_exc.SQLAlchemyError as e:
                await session.rollback()
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def update_attr(self, id: int, column: str, value: Any):
        async with self.session_factory() as session:
            try:
                db_obj = await session.get(self.model, id)
                if not db_obj:
                    raise HTTPException(
                        status_code=404,
                        detail=f"{self.model.__tablename__.capitalize()} with id {id} not found.",
                    )
                setattr(db_obj, column, value)
                session.add(db_obj)
                await session.commit()
                await session.refresh(db_obj)
                return db_obj
            except sa_exc.SQLAlchemyError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def whole_update(self, id: int, schema: T):
        async with self.session_factory() as session:
            try:
                db_obj = await session.get(self.model, id)
                if not db_obj:
                    raise HTTPException(
                        status_code=404,
                        detail=f"{self.model.__tablename__.capitalize()} with id {id} not found.",
                    )

                obj_data = schema.model_dump()
                for key, value in obj_data.items():
                    setattr(db_obj, key, value)

                session.add(db_obj)
                await session.commit()
                await session.refresh(db_obj)
                return db_obj
            except sa_exc.SQLAlchemyError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def delete_by_id(self, id: int):
        async with self.session_factory() as session:
            try:
                db_obj = await session.get(self.model, id)
                if not db_obj:
                    raise NotFoundError(
                        detail=f"{self.model.__tablename__.capitalize()} with id {id} not found."
                    )
                await session.delete(db_obj)
                await session.commit()
                return {
                    "message": f"{self.model.__tablename__.capitalize()} with id {id} deleted successfully."
                }
            except sa_exc.SQLAlchemyError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def get_unique_values(self, schema: T) -> dict:
        async with self.session_factory() as session:
            try:
                schema_as_dict: dict = schema.model_dump(exclude_none=True)

                field_name = schema_as_dict.get("field_name")
                ordering: str = schema_as_dict.get("ordering", config.ORDERING)
                page = schema_as_dict.get("page", config.PAGE)
                page_size = schema_as_dict.get("page_size", config.PAGE_SIZE)
                search_term = schema_as_dict.get("search")

                column = getattr(self.model, field_name)
                # SQLModel select distinct
                query = select(column).distinct()

                if search_term:
                    query = query.where(column.ilike(f"%{search_term}%"))

                if ordering.startswith("-"):
                    query = query.order_by(column.desc())
                else:
                    query = query.order_by(column.asc())

                # Count distinct
                count_query = select(func.count(distinct(column)))
                if search_term:
                    count_query = count_query.where(column.ilike(f"%{search_term}%"))

                count_res = await session.execute(count_query)
                total_count = count_res.scalar()

                if page_size != "all":
                    query = query.offset((page - 1) * page_size).limit(page_size)

                vals_res = await session.execute(query)
                values = vals_res.scalars().all()

                return {
                    "founds": values,
                    "search_options": {
                        "page": page,
                        "page_size": page_size,
                        "ordering": ordering,
                        "total_count": total_count,
                    },
                }
            except sa_exc.SQLAlchemyError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e

    async def close_scoped_session(self):
        # With async session factory/context manager, typically we trust the context manager
        # If we need manual close:
        pass

    def build_nested_search(
        self, search_fields: list[str], search_term: str, query: Any
    ):
        search_conditions = []
        for field in search_fields:
            if "." in field:
                query, field_name = get_field(self.model, field, query)
                condition = field_name.ilike(f"%{search_term}%")
                search_conditions.append(condition)
            else:
                if hasattr(self.model, field):
                    condition = getattr(self.model, field).ilike(f"%{search_term}%")
                    search_conditions.append(condition)

        if search_conditions:
            return query, or_(*search_conditions)
        return query, None
