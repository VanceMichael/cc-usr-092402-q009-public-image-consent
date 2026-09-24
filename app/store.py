"""SQLite 持久化：只做存取与行/字典转换，不含业务判定。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(database_path: str | None = None) -> sqlite3.Connection:
    path = Path(database_path or os.getenv("DATABASE_PATH", "data/app.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_DDL)
    here = Path(__file__).resolve().parent.parent / "migrations"
    done = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for path in sorted(here.glob("*.sql")):
        if path.stem in done:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (path.stem,)
        )
    connection.commit()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


# ---------------------------------------------------------------- 单行读取

def get(connection: sqlite3.Connection, table: str, key_field: str, key: str) -> dict | None:
    row = connection.execute(
        f"SELECT * FROM {table} WHERE {key_field} = ?", (key,)
    ).fetchone()
    return dict(row) if row else None


def insert(connection: sqlite3.Connection, table: str, row: dict) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values())
    )


# ---------------------------------------------------------------- 聚合读取

def claims_for_work(connection: sqlite3.Connection, work_ref: str) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM person_claims WHERE work_ref = ?", (work_ref,)
    ).fetchall()
    return [dict(r) for r in rows]


def grants_ledger(connection: sqlite3.Connection) -> list[dict]:
    """全部授权附最新登记的撤回（按登记时间取最新一条，用于 as-of 复算）。"""
    rows = connection.execute(
        """
        SELECT g.*, r.revoked_at AS revoked_at,
               r.recorded_at AS revocation_recorded_at
          FROM grants g
          LEFT JOIN grant_revocations r
            ON r.grant_id = g.grant_id
           AND r.recorded_at = (
               SELECT MAX(recorded_at) FROM grant_revocations WHERE grant_id = g.grant_id
           )
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ("purposes", "channels", "regions"):
            d[key] = loads(d[key])
        out.append(d)
    return out


def guardianships_for(connection: sqlite3.Connection, child_ref: str) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM guardianships WHERE child_ref = ?", (child_ref,)
    ).fetchall()
    return [dict(r) for r in rows]


def version_presence(connection: sqlite3.Connection, version_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM version_presence WHERE version_id = ?", (version_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def replace_version_presence(connection: sqlite3.Connection, version_id: str,
                             entries: list) -> None:
    """entries 为 domain.Presence 列表（按 duck typing 取属性）。"""
    connection.execute("DELETE FROM version_presence WHERE version_id = ?", (version_id,))
    for e in entries:
        insert(connection, "version_presence", {
            "version_id": version_id,
            "person_ref": e.person_ref,
            "present": 1,
            "masked": int(e.masked),
            "role": e.role,
            "bbox": dumps(list(e.bbox)) if e.bbox is not None else None,
            "detail": "；".join(e.details),
        })


def active_publications_using_grant(connection: sqlite3.Connection,
                                    grant_id: str) -> list[dict]:
    """状态 active 且冻结证据中命中过指定授权的发布。"""
    rows = connection.execute(
        "SELECT * FROM publications WHERE status = 'active'"
    ).fetchall()
    hit = []
    for r in rows:
        evidence = loads(r["evidence"])
        if any(grant_id in (p.get("matched_grants") or []) for p in evidence.get("presence", [])):
            hit.append(dict(r))
    return hit


def open_dispositions(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM disposition_items WHERE status = 'open' ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]
