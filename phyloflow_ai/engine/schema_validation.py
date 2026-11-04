import json
import re
from pathlib import Path


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "schemas"


class SchemaValidationError(ValueError):
    pass


def validate_schema(document, schema_name):
    schema_path = SCHEMA_ROOT / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    _validate(document, schema, "$", schema_path.parent)


def _validate(value, schema, location, schema_root):
    if "$ref" in schema:
        referenced = json.loads((schema_root / schema["$ref"]).read_text(encoding="utf-8"))
        return _validate(value, referenced, location, schema_root)
    expected = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "boolean": bool,
    }
    if expected in types and not isinstance(value, types[expected]):
        raise SchemaValidationError(f"{location} must be {expected}.")
    if "const" in schema and value != schema["const"]:
        raise SchemaValidationError(f"{location} must equal {schema['const']!r}.")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{location} has an unsupported value.")
    if isinstance(value, str) and schema.get("pattern") and not re.match(schema["pattern"], value):
        raise SchemaValidationError(f"{location} does not match the required pattern.")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise SchemaValidationError(f"{location}.{name} is required.")
        for name, child_schema in schema.get("properties", {}).items():
            if name in value:
                _validate(value[name], child_schema, f"{location}.{name}", schema_root)
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{location}[{index}]", schema_root)
