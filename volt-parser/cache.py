from __future__ import annotations
import sqlite3
from pathlib import Path
import json
from typing import Any, Optional

CACHE_PATH = Path.home() / ".cache" / "volt_parser" / "cache.sqlite"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)


class Cache:

    def __init__(self, path: Path = CACHE_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS kv (
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP)"""
        )
        self.conn.commit()

    def get(self, key: str) -> Optional[Any]:
        row = self.conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value: Any):
        self.conn.execute(
            "REPLACE INTO kv (k, v) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

CACHE = Cache()
