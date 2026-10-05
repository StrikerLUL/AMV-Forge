"""Datenbank-Tabellen (SQLModel = SQLAlchemy + Pydantic in einem)."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Season(SQLModel, table=True):
    """Eine Staffel aus Jellyfin oder einem Ordner."""

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)  # z. B. "folder:D:/Anime/Horimiya" oder "jellyfin:<id>"
    source: str  # "folder" oder "jellyfin"
    source_id: str  # Ordnerpfad oder Jellyfin-Item-ID der Staffel
    title: str
    season_number: int = 1
    anilist_id: Optional[int] = None
    mal_id: Optional[int] = None
    anilist_title: Optional[str] = None
    genres: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tags: list[dict] = Field(default_factory=list, sa_column=Column(JSON))  # [{"name": ..., "rank": ...}]
    metadata_done: bool = False
    indexed_at: Optional[datetime] = None
    mood_signature: Optional[str] = None  # Phase 4: Stimmung aller Clips berechnet (mit diesen Signalen)


class Episode(SQLModel, table=True):
    """Eine Folge. skips_source und scenes_signature merken sich, was schon berechnet ist."""

    __table_args__ = (UniqueConstraint("season_id", "number"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    number: int
    title: Optional[str] = None
    path: Optional[str] = None  # lokale Datei (bei Jellyfin der Download im Cache)
    source_item_id: Optional[str] = None  # Jellyfin-Item-ID
    duration: Optional[float] = None
    file_size: Optional[int] = None
    file_mtime_ns: Optional[int] = None
    filler: bool = False
    recap: bool = False
    skips_source: Optional[str] = None  # "aniskip", "fingerprint", "none" oder None = noch nicht gesucht
    scenes_signature: Optional[str] = None  # Fingerabdruck von Datei + Einstellungen + OP/ED
    motion_signature: Optional[str] = None  # Phase 3: Bewegung für alle Clips dieser Szenen gemessen
    # Phase 4: Standbilder + CLIP-Embeddings, CLIP-Vergleich mit den Prompts, Ton, Untertitel
    visual_signature: Optional[str] = None
    tags_signature: Optional[str] = None
    audio_signature: Optional[str] = None
    subtitle_signature: Optional[str] = None
    subtitle_source: Optional[str] = None  # z. B. "Folge.de.ass", "Spur 2 (ger)" oder "keine"


class SkipSegment(SQLModel, table=True):
    """Bereich, der nicht ins Edit darf: Opening, Ending, Recap."""

    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    kind: str  # "op", "ed", "recap", "mixed-op", "mixed-ed"
    start: float
    end: float
    source: str  # "aniskip" oder "fingerprint"


class Clip(SQLModel, table=True):
    """Eine Szene ohne OP/ED mit allem, was die Analyse über sie weiß."""

    id: Optional[int] = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    start: float
    end: float
    motion: Optional[float] = None  # durchschnittliche Bewegung (Optical Flow)
    motion_peak: Optional[float] = None  # Zeitpunkt der stärksten Bewegung (Sekunden in der Folge)
    # Phase 4: Bild
    thumbnail: Optional[str] = None  # Vorschaubild (JPG, mittleres Standbild)
    brightness_min: Optional[float] = None  # Helligkeit 0-1 des dunkelsten / hellsten Standbilds
    brightness_max: Optional[float] = None
    contrast: Optional[float] = None
    sharpness: Optional[float] = None
    clip_tags: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # CLIP: Gruppe -> Wahrscheinlichkeit
    clip_top: Optional[str] = None  # CLIP-Satz, der am besten passt
    # Phase 4: Ton und Untertitel
    loudness: Optional[float] = None  # dBFS
    speech: Optional[float] = None  # Anteil Sprache 0-1 (Silero VAD)
    subtitle: Optional[str] = None  # Untertiteltext im Clip
    dialog_tags: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # Untertitel: Gruppe -> Wahrscheinlichkeit
    # Phase 4: Ergebnis
    mood: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # romance, action, sad, funny, calm je 0-1
    quality: Optional[float] = None  # 0-1
    quality_issue: Optional[str] = None  # Grund fürs Aussortieren, z. B. "schwarz" oder "text"
    dialog: Optional[bool] = None  # wird im Clip geredet?


class Character(SQLModel, table=True):
    """Figur aus AniList, die Bilder brauchen wir in Phase 5 für die Gesichtserkennung."""

    id: Optional[int] = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    anilist_id: int
    name: str
    role: str  # MAIN, SUPPORTING, BACKGROUND
    image_url: Optional[str] = None


class Song(SQLModel, table=True):
    """Ergebnis der Song-Analyse (Phase 3). Ein Song wird nur einmal analysiert."""

    id: Optional[int] = Field(default=None, primary_key=True)
    path: str = Field(index=True, unique=True)
    file_size: int
    file_mtime_ns: int
    settings_signature: str  # Einstellungen, mit denen analysiert wurde
    analyzer: str  # "allin1" oder "librosa"
    duration: float
    bpm: float
    analysis: dict = Field(default_factory=dict, sa_column=Column(JSON))  # SongAnalysis als JSON
    analyzed_at: datetime = Field(default_factory=_now)


class ApiCache(SQLModel, table=True):
    """Jede Antwort von AniList, Jikan und AniSkip landet hier und wird nie zweimal abgefragt."""

    key: str = Field(primary_key=True)
    service: str = Field(index=True)
    url: str
    status_code: int
    body: str
    fetched_at: datetime = Field(default_factory=_now)
