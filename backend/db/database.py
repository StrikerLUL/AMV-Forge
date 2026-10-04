"""Verbindung zur SQLite-Datenbank."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

from backend.db import models  # noqa: F401  (registriert die Tabellen)

log = logging.getLogger(__name__)


def add_missing_columns(engine: Engine) -> list[str]:
    """Neue Spalten aus models.py in eine ältere Datenbank eintragen.

    create_all legt nur fehlende Tabellen an, keine fehlenden Spalten. Neue Spalten sind bei uns
    immer optional (None = noch nicht berechnet), deshalb reicht ein einfaches ALTER TABLE.
    """
    added: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"Die Datenbank ist zu alt (Spalte {table.name}.{column.name} fehlt). "
                        "Lösch data/amv_forge.sqlite und indexiere neu."
                    )
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
                added.append(f"{table.name}.{column.name}")
    if added:
        log.info("Datenbank erweitert um: %s", ", ".join(added))
    return added


def get_engine(path: Path) -> Engine:
    """Öffnet (oder erstellt) die Datenbank und legt fehlende Tabellen und Spalten an."""
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)
    return engine
