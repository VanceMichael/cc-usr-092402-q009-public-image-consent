
"""SQLite 连接辅助。"""

import os
import sqlite3
from pathlib import Path

from flask import current_app, g


def database_path() -> Path:
    configured = current_app.config.get("DATABASE_PATH") or os.getenv(
        "DATABASE_PATH", "data/app.sqlite3"
    )
    return Path(configured)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        path = database_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_exc=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()
