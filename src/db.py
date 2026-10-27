"""SQLite 接続ヘルパ。DB は db/tdf47.sqlite 一本（設計書「唯一の状態」）。"""

from __future__ import annotations

import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "tdf47.sqlite")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: str = SCHEMA_PATH) -> None:
    """schema.sql を適用する（CREATE TABLE IF NOT EXISTS なので冪等）。"""
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
