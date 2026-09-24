
import os
import sqlite3
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _applied_versions(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchall()
    if not rows:
        return set()
    return {
        row[0]
        for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }


def migrate(database_path: Path | None = None) -> list[str]:
    database_path = database_path or Path(
        os.getenv("DATABASE_PATH", "data/app.sqlite3")
    )
    applied: list[str] = []
    with _connect(database_path) as connection:
        done = _applied_versions(connection)
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.stem in done:
                continue
            connection.executescript(sql_file.read_text(encoding="utf-8"))
            applied.append(sql_file.stem)
    if applied:
        print(f"数据库迁移完成：{database_path}（应用 {', '.join(applied)}）")
    else:
        print(f"数据库已是最新：{database_path}")
    return applied


if __name__ == "__main__":
    migrate()
