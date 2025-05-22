from __future__ import annotations
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from .schema import schema
from typing import Any, List


def write_json(data: List[dict[str, Any]], output: Path) -> None:
    Draft202012Validator(schema).validate(data)
    output.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def generate_json(companies: list[dict[str, Any]]) -> str:
    Draft202012Validator(schema).validate(companies)
    return json.dumps(companies, indent=2, ensure_ascii=False)