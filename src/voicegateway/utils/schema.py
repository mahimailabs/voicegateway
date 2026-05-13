from pydantic import BaseModel, Field, create_model


def make_optional(model: type[BaseModel]) -> type[BaseModel]:
    """Make all fields of a Pydantic model optional while preserving validation constraints."""
    fields = {}

    model_schema = model.model_json_schema()
    properties = model_schema.get("properties", {})

    for name, field in model.model_fields.items():
        field_schema = properties.get(name, {})

        constraints = {}

        if "minimum" in field_schema:
            constraints["ge"] = field_schema["minimum"]
        if "maximum" in field_schema:
            constraints["le"] = field_schema["maximum"]
        if "exclusiveMinimum" in field_schema:
            constraints["gt"] = field_schema["exclusiveMinimum"]
        if "exclusiveMaximum" in field_schema:
            constraints["lt"] = field_schema["exclusiveMaximum"]
        if "minLength" in field_schema:
            constraints["min_length"] = field_schema["minLength"]
        if "maxLength" in field_schema:
            constraints["max_length"] = field_schema["maxLength"]
        if "pattern" in field_schema:
            constraints["pattern"] = field_schema["pattern"]
        if "description" in field_schema:
            constraints["description"] = field_schema["description"]

        if "error_messages" in field_schema:
            constraints["error_messages"] = field_schema["error_messages"]

        fields[name] = (field.annotation | None, Field(default=None, **constraints))

    return create_model(f"Optional{model.__name__}", **fields, __base__=model)
