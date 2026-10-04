"""Eine Datenbank aus Phase 2 bekommt die neuen Spalten automatisch."""

import sqlite3
from pathlib import Path

from sqlmodel import Session, select

from backend.db import get_engine
from backend.db.models import Clip, Episode


def test_old_database_gets_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "alt.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE season (id INTEGER PRIMARY KEY, key VARCHAR NOT NULL, source VARCHAR NOT NULL,
            source_id VARCHAR NOT NULL, title VARCHAR NOT NULL, season_number INTEGER NOT NULL,
            anilist_id INTEGER, mal_id INTEGER, anilist_title VARCHAR, genres JSON, tags JSON,
            metadata_done BOOLEAN NOT NULL, indexed_at DATETIME);
        CREATE TABLE episode (id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL, number INTEGER NOT NULL,
            title VARCHAR, path VARCHAR, source_item_id VARCHAR, duration FLOAT, file_size INTEGER,
            file_mtime_ns INTEGER, filler BOOLEAN NOT NULL, recap BOOLEAN NOT NULL, skips_source VARCHAR,
            scenes_signature VARCHAR);
        CREATE TABLE clip (id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL, start FLOAT NOT NULL,
            "end" FLOAT NOT NULL);
        INSERT INTO season VALUES (1, 'folder:x', 'folder', 'x', 'Horimiya', 1, 124080, 42897, 'Horimiya',
            '[]', '[]', 1, NULL);
        INSERT INTO episode VALUES (1, 1, 1, NULL, 'x.mkv', NULL, 1428.0, 1, 1, 0, 0, 'fingerprint', 'abc');
        INSERT INTO clip VALUES (1, 1, 10.0, 12.5);
        """
    )
    con.commit()
    con.close()

    engine = get_engine(path)
    with Session(engine) as session:
        clip = session.exec(select(Clip)).one()
        assert (clip.start, clip.end, clip.motion, clip.motion_peak) == (10.0, 12.5, None, None)
        assert session.exec(select(Episode)).one().motion_signature is None

    # Zweites Öffnen ändert nichts mehr
    from backend.db.database import add_missing_columns

    assert add_missing_columns(engine) == []
