from __future__ import annotations

import json
from pathlib import Path
import importlib.resources as pkg_resources

_schema_path = Path(__file__).with_suffix(".json")
if not _schema_path.exists():
    _schema_path = Path(pkg_resources.files(__package__) / "schema.json")

schema = json.loads(_schema_path.read_text(encoding="utf-8"))
