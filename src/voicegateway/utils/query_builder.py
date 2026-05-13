from enum import Enum

from sqlalchemy.orm import aliased
from sqlmodel import and_, or_

SQLALCHEMY_QUERY_MAPPER = {
    "eq": "__eq__",
    "ne": "__ne__",
    "lt": "__lt__",
    "lte": "__le__",
    "gt": "__gt__",
    "gte": "__ge__",
}


def dict_to_sqlalchemy_filter_options(model_class, search_option_dict):
    sql_alchemy_filter_options = []
    copied_dict = search_option_dict.copy()
    for key in search_option_dict:
        attr = getattr(model_class, key, None)
        if attr is None:
            continue
        option_from_dict = copied_dict.pop(key)
        if type(option_from_dict) in [int, float]:
            sql_alchemy_filter_options.append(attr == option_from_dict)

        elif isinstance(option_from_dict, Enum):
            sql_alchemy_filter_options.append(attr == option_from_dict.value)

        elif type(option_from_dict) in [str]:
            if "," in option_from_dict:
                parts = [
                    part.strip() for part in option_from_dict.split(",") if part.strip()
                ]
                like_conditions = [attr.like(f"%{part}%") for part in parts]
                sql_alchemy_filter_options.append(or_(*like_conditions))
            else:
                sql_alchemy_filter_options.append(
                    attr.like(f"%{option_from_dict.strip()}%")
                )
        elif type(option_from_dict) in [bool]:
            sql_alchemy_filter_options.append(attr == option_from_dict)

    for custom_option in copied_dict:
        if "__" not in custom_option:
            continue
        key, command = custom_option.split("__")
        attr = getattr(model_class, key, None)
        if attr is None:
            continue
        option_from_dict = copied_dict[custom_option]
        if command == "in":
            sql_alchemy_filter_options.append(
                attr.in_([option.strip() for option in option_from_dict.split(",")])
            )
        elif command in SQLALCHEMY_QUERY_MAPPER.keys():
            sql_alchemy_filter_options.append(
                getattr(attr, SQLALCHEMY_QUERY_MAPPER[command])(option_from_dict)
            )
        elif command == "isnull":
            bool_command = "__eq__" if option_from_dict else "__ne__"
            sql_alchemy_filter_options.append(getattr(attr, bool_command)(None))

    return and_(True, *sql_alchemy_filter_options)


def get_field(model, field_name, query):
    if "." not in field_name:
        return query, getattr(model, field_name)

    path_parts = field_name.split(".")
    current_model = model
    current_alias = None
    alias_map = {}

    for i, part in enumerate(path_parts[:-1]):
        path_key = ".".join(path_parts[: i + 1])
        relationship_attr = getattr(current_model, part)

        if path_key in alias_map:
            aliased_model = alias_map[path_key]
        else:
            aliased_model = aliased(relationship_attr.property.mapper.class_)
            alias_map[path_key] = aliased_model

            if current_alias is None:
                query = query.join(aliased_model, relationship_attr)
            else:
                query = query.join(aliased_model, getattr(current_alias, part))

        current_model = relationship_attr.property.mapper.class_
        current_alias = aliased_model

    return query, getattr(current_alias, path_parts[-1])
