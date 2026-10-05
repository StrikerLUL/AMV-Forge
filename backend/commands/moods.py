"""CLI-Befehl für Phase 4: Was hat die Stimmungserkennung gefunden? Mit Kontaktbögen zum Anschauen."""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path

from sqlmodel import Session, select

from backend.analysis.mood import MOODS, dominant, mood_match
from backend.commands.common import fmt_time
from backend.config.settings import Settings
from backend.config.styles import available_styles, load_style
from backend.db import get_engine
from backend.db.models import Clip, Episode, Season
from backend.render.contact_sheet import Tile, contact_sheet

log = logging.getLogger("amv_forge")


def add_mood_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    moods = sub.add_parser("moods", help="Phase 4: Stimmung der Clips anzeigen (mit Kontaktbögen)")
    moods.add_argument("--season", type=int, required=True, help="DB-ID der Staffel (zeigt 'status')")
    moods.add_argument("--style", choices=available_styles(), default=None,
                       help="Nur die Clips zeigen, die am besten zu diesem Stil passen")
    moods.add_argument("--top", type=int, default=24, help="So viele Clips pro Kontaktbogen")
    moods.add_argument("--no-sheet", action="store_true", help="Keine Kontaktbögen (JPG) schreiben")
    moods.add_argument("--out-dir", type=Path, default=Path("data") / "renders", help="Ordner für die Kontaktbögen")
    moods.add_argument("--config", type=Path, default=None)
    moods.set_defaults(handler=run_moods)


def _short(text: str | None, limit: int = 60) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _describe(clip: Clip, ep: Episode, value: float) -> str:
    parts = [f"Folge {ep.number:>2} {fmt_time(clip.start)} ({clip.end - clip.start:4.1f} s)  {value:.2f}"]
    if clip.clip_top:
        parts.append(f"Bild: {clip.clip_top}")
    if clip.subtitle:
        parts.append(f'Text: "{_short(clip.subtitle)}"')
    if clip.speech is not None:
        parts.append(f"Sprache {clip.speech:.0%}")
    return " | ".join(parts)


def run_moods(args: argparse.Namespace, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    with Session(engine) as session:
        season = session.get(Season, args.season)
        if season is None:
            raise ValueError(f"Keine Staffel mit DB-ID {args.season}. 'status' zeigt alle.")
        rows = list(session.exec(
            select(Clip, Episode).join(Episode, Clip.episode_id == Episode.id).where(Episode.season_id == season.id)
        ).all())
    if not rows or season.mood_signature is None or any(c.mood is None for c, _ in rows):
        raise ValueError("Für diese Staffel gibt es noch keine Stimmung. Erst 'index' laufen lassen (Phase 4).")

    episodes = {ep.id: ep for _, ep in rows}.values()
    with_clip = {ep.number for c, ep in rows if c.clip_tags}
    with_speech = {ep.number for c, ep in rows if c.speech is not None}
    with_subs = sorted({ep.number for ep in episodes if ep.subtitle_source and ep.subtitle_source != "keine"})
    with_dialog = {ep.number for c, ep in rows if c.dialog_tags}
    n = len(episodes)
    log.info("%s: %d Clips aus %d Folgen", season.anilist_title or season.title, len(rows), n)
    log.info("Signale: Bild (CLIP) %d/%d Folgen, Sprache %d/%d, Untertitel %d/%d (davon eingeordnet %d), Bewegung %d/%d",
             len(with_clip), n, len(with_speech), n, len(with_subs), n, len(with_dialog),
             len({ep.number for c, ep in rows if c.motion is not None}), n)

    def ok(clip: Clip) -> bool:
        return clip.quality is None or clip.quality >= settings.quality.min_score

    usable = [(c, ep) for c, ep in rows if ok(c)]
    issues = Counter(c.quality_issue or "niedrig" for c, _ in rows if not ok(c))
    log.info("Aussortiert wegen Bildqualität: %d (%s)", len(rows) - len(usable),
             ", ".join(f"{k} {v}" for k, v in issues.most_common()) or "keine")
    counts = Counter(dominant(c.mood or {}) for c, _ in usable)
    log.info("Stärkste Stimmung pro Clip: %s", ", ".join(f"{m} {counts.get(m, 0)}" for m in MOODS))

    if args.style:
        style = load_style(args.style, settings.planner.weights)
        rankings = {style.name: sorted(((mood_match(c.mood or {}, style.mood), c, ep) for c, ep in usable),
                                       key=lambda t: -t[0])}
    else:
        rankings = {m: sorted((((c.mood or {}).get(m, 0.0), c, ep) for c, ep in usable), key=lambda t: -t[0])
                    for m in MOODS}

    for name, ranked in rankings.items():
        log.info("")
        log.info("Top %s:", name)
        for value, clip, ep in ranked[:5]:
            log.info("  %s", _describe(clip, ep, value))
        if args.no_sheet:
            continue
        tiles = [Tile(Path(c.thumbnail) if c.thumbnail else None, f"F{ep.number} {fmt_time(c.start)} {v:.2f}")
                 for v, c, ep in ranked[: args.top]]
        out = contact_sheet(tiles, args.out_dir / f"stimmung_s{season.id}_{name}.jpg")
        log.info("  Kontaktbogen: %s", out)
