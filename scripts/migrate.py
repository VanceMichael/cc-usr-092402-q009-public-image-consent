
import os
import sqlite3
from pathlib import Path


database_path = Path(os.getenv("DATABASE_PATH", "data/app.sqlite3"))
database_path.parent.mkdir(parents=True, exist_ok=True)

migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
with sqlite3.connect(database_path) as connection:
    applied = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchall()
    }
    if not applied:
        connection.executescript(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    done = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.stem in done:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (path.stem,)
        )
        print(f"应用迁移：{path.stem}")
print(f"数据库迁移完成：{database_path}")
