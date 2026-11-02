"""Phase 7: Welche Songs passen zu einer Staffel und einem Stil? Die besten mit kurzer Begründung.

Jeder Song bekommt fünf Teilnoten (je 0-1), gewichtet gemittelt (suggest.weights in default.yaml):
  mood     = Passt die Stimmung des Songs zum Stil (mood: in backend/styles/<stil>.yaml)?
  season   = Klingt der Song wie die Szenen der Staffel, die für den Stil in Frage kommen (der Pool)?
  tempo    = Liegt das Tempo im Bereich des Stils (bpm:), notfalls im halben oder doppelten Tempo?
  drop     = Liegt ein Drop im Ausschnitt, den 'edit' nehmen würde, und will der Stil das (song: drop:)?
  material = Gibt es genug passende Clips für die Schnitte im Ausschnitt (wichtig mit --characters)?
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from backend.analysis.mood import MOODS, mood_match
from backend.analysis.music.energy import Drop
from backend.analysis.music.structure import SongAnalysis
from backend.config.settings import Settings
from backend.config.styles import StyleProfile
from backend.planner.scoring import Scorable, character_tiers, style_pool
from backend.planner.song_slots import build_song_slots, choose_edit_start, style_cuts, tempo_factor
from backend.sources.music import song_label, song_name

MOOD_WORDS = {"romance": "romantisch", "action": "actiongeladen", "sad": "traurig", "funny": "fröhlich",
              "calm": "ruhig"}
GOOD, WEAK = 0.7, 0.45  # ab GOOD wird ein Teil als Grund genannt, unter WEAK als Einwand
# Die Stimmung eines Songs mischt zwei Signale und ist deshalb selten ganz eindeutig: schon ab hier ein Grund
MOOD_GOOD = 0.6


@dataclass(frozen=True)
class SeasonProfile:
    """Die Szenen einer Staffel, die für einen Stil in Frage kommen."""

    title: str
    clips: int  # Clips der Staffel (ohne aussortierte)
    pool: int  # davon kommen für den Stil (und die Figuren) in Frage
    mood: dict[str, float]  # durchschnittliche Stimmung dieser Clips


@dataclass(frozen=True)
class LibrarySong:
    path: str
    title: str
    artist: str | None
    analysis: SongAnalysis
    mood: dict[str, float]
    key: str | None = None
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return song_label(self.artist, self.title)

    @property
    def main_artist(self) -> str:
        """Interpret für suggest.max_per_artist (bei YouTube-Downloads der aus dem Titel, nicht der Kanal)."""
        return (song_name(self.artist, self.title)[0] or "").lower()


@dataclass(frozen=True)
class Suggestion:
    song: LibrarySong
    score: float  # 0-1
    parts: dict[str, float]  # Teilnoten 0-1
    weights: dict[str, float]  # tatsächlich benutzte Gewichte (0 = zählt bei diesem Stil nicht)
    window_start: float  # hier würde 'edit' im Song starten
    cuts: int  # so viele Schnitte hätte das Edit
    factor: float  # Tempo-Faktor (2 = im halben Tempo gezählt)
    drop: Drop | None  # stärkster Drop im Ausschnitt
    reasons: list[str]
    concerns: list[str]


def season_profile(candidates: Sequence[Scorable], style: StyleProfile, title: str,
                   characters: Sequence[int] = ()) -> SeasonProfile:
    """Pool wie bei 'edit': die besten Clips nach Stimmung des Stils, mit --characters nur Clips mit allen Figuren."""
    among = sorted(character_tiers(candidates, characters)[-1]) if characters else None
    pool = style_pool(candidates, style.mood, style.pool, among) if (among is None or among) else set()
    if characters:
        pool &= character_tiers(candidates, characters)[0]
    moods = [candidates[i].mood for i in pool if candidates[i].mood]
    average = {m: round(sum(x[m] for x in moods) / len(moods), 3) for m in MOODS} if moods else {}
    return SeasonProfile(title=title, clips=len(candidates), pool=len(pool), mood=average)


def profile_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Wie ähnlich zwei Stimmungs-Profile sind, nur die Form (welche Stimmungen über/unter dem eigenen Schnitt
    liegen), nicht die Höhe: Clip- und Song-Stimmungen sind verschieden gemessen. 0 = gegensätzlich, 1 = gleich."""
    if not a or not b:
        return 0.5
    x = [a.get(m, 0.5) for m in MOODS]
    y = [b.get(m, 0.5) for m in MOODS]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    x, y = [v - mx for v in x], [v - my for v in y]
    norm = math.sqrt(sum(v * v for v in x)) * math.sqrt(sum(v * v for v in y))
    if norm < 1e-9:
        return 0.5
    return round((1 + sum(p * q for p, q in zip(x, y)) / norm) / 2, 3)


def standout(mood: dict[str, float], count: int = 2) -> list[str]:
    """Die Stimmungen, die über dem eigenen Durchschnitt liegen, stärkste zuerst."""
    if not mood:
        return []
    mean = sum(mood.get(m, 0.5) for m in MOODS) / len(MOODS)
    ranked = sorted((m for m in MOODS if mood.get(m, 0.5) > mean), key=lambda m: -mood.get(m, 0.5))
    return ranked[:count]


def tempo_fit(bpm: float, wanted: tuple[float, float] | None, tolerance: float,
              halftime: float) -> tuple[float, float]:
    """(Note 0-1, Tempo-Faktor). Im Bereich = 1, tolerance (Anteil) daneben = 0, halbes/doppeltes Tempo mal halftime."""
    factor = tempo_factor(bpm, wanted)
    if wanted is None or bpm <= 0:
        return 1.0, factor
    low, high = wanted
    counted = bpm / factor
    distance = low / counted if counted < low else (counted / high if counted > high else 1.0)
    fit = max(0.0, 1.0 - (distance - 1.0) / max(1e-6, tolerance))
    return round(fit * (halftime if factor != 1.0 else 1.0), 3), factor


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _words(moods: Sequence[str]) -> str:
    return " und ".join(MOOD_WORDS[m] for m in moods)


def rate(song: LibrarySong, style: StyleProfile, profile: SeasonProfile, settings: Settings,
         length: float) -> Suggestion:
    cfg = settings.suggest
    analysis = song.analysis
    fit, factor = tempo_fit(analysis.bpm, style.bpm, cfg.tempo_tolerance, cfg.halftime_factor)
    cuts_cfg = style_cuts(settings.cuts, style.cuts, factor)
    start = choose_edit_start(analysis, length, None, cuts_cfg.drop_position)
    cuts = len(build_song_slots(analysis, start, length, cuts_cfg, settings.quick.min_slot_seconds))
    drops = [d for d in analysis.drops if start <= d.time < start + length]
    drop = max(drops, key=lambda d: d.strength) if drops else None
    drop_level = min(1.0, drop.strength / cfg.drop_full) if drop else 0.0

    parts = {
        "mood": round(mood_match(song.mood, style.mood), 3),
        "season": profile_similarity(song.mood, profile.mood),
        "tempo": fit,
        "drop": round(drop_level if style.song_drop >= 0 else 1.0 - drop_level, 3),
        "material": round(min(1.0, profile.pool / max(1.0, cuts * cfg.clips_per_cut)), 3),
    }
    weights = dict(cfg.weights)
    weights["tempo"] = weights.get("tempo", 0.0) if style.bpm is not None else 0.0
    weights["drop"] = weights.get("drop", 0.0) * abs(style.song_drop)
    weights["season"] = weights.get("season", 0.0) if profile.mood else 0.0
    total = sum(w for w in weights.values() if w > 0)
    score = sum(weights[k] * parts[k] for k in parts if weights.get(k, 0) > 0) / total if total > 0 else 0.0

    reasons, concerns = _explain(song, style, profile, parts, weights, factor, drop, cuts)
    return Suggestion(song, round(score, 3), parts, weights, start, cuts, factor, drop, reasons, concerns)


def _explain(song: LibrarySong, style: StyleProfile, profile: SeasonProfile, parts: dict[str, float],
             weights: dict[str, float], factor: float, drop: Drop | None,
             cuts: int) -> tuple[list[str], list[str]]:
    """Gründe (gute Teilnoten, wichtigste zuerst) und Einwände (schwache Teilnoten) als kurze Sätze."""
    bpm = song.analysis.bpm
    song_moods = standout(song.mood)
    good: list[tuple[float, str]] = []
    bad: list[tuple[float, str]] = []

    def add(part: str, positive: str | None, negative: str | None) -> None:
        w, v = weights.get(part, 0.0), parts[part]
        if w <= 0:
            return
        if v >= (MOOD_GOOD if part == "mood" else GOOD) and positive:
            good.append((w * v, positive))
        elif v < WEAK and negative:
            bad.append((w * (1 - v), negative))

    pool_moods = standout(profile.mood)
    scenes = f"die {style.name}-Szenen von {profile.title}"
    # Klingen Song und Szenen gleich, wird daraus ein Grund statt zwei fast gleicher
    merged = bool(pool_moods) and pool_moods == song_moods and parts["season"] >= GOOD
    clap = song.features.get("clap_top")
    heard = f' (CLAP: "{clap}")' if clap else ""
    sound = f"klingt {_words(song_moods)}" if song_moods else "klingt unauffällig"
    add("mood", f"{sound}{' wie ' + scenes if merged else ''}{heard}", f"{sound}{heard}, nicht nach {style.name}")
    if pool_moods:
        add("season", None if merged else f"wie {scenes} ({_words(pool_moods)})",
            f"{scenes} sind eher {_words(pool_moods)}")

    if style.bpm is not None:
        low, high = style.bpm
        counted = bpm / factor
        how = {2.0: f", im halben Tempo gezählt (wie {counted:.0f})", 0.5: f", im doppelten Tempo gezählt (wie "
               f"{counted:.0f})"}.get(factor, "")
        where = "passt zu" if low <= counted <= high else "liegt nah an"
        add("tempo", f"{bpm:.0f} BPM {where} {style.name} ({low:.0f}-{high:.0f}){how}",
            f"{bpm:.0f} BPM passt schlecht zu {style.name} ({low:.0f}-{high:.0f})")

    if style.song_drop > 0:
        add("drop", f"Drop bei {_mmss(drop.time)} im Ausschnitt" if drop else None, "kein Drop im Ausschnitt")
    elif style.song_drop < 0:
        add("drop", None, f"harter Drop bei {_mmss(drop.time)}" if drop else None)

    add("material", None, f"nur {profile.pool} passende Clips für {cuts} Schnitte")
    reasons = [text for _, text in sorted(good, key=lambda t: -t[0])][:3]
    concerns = [text for _, text in sorted(bad, key=lambda t: -t[0])][:2]
    return reasons, concerns


def suggest(songs: Sequence[LibrarySong], style: StyleProfile, profile: SeasonProfile, settings: Settings,
            length: float, top: int) -> list[Suggestion]:
    """Die besten Songs, höchstens suggest.max_per_artist pro Interpret. Zu kurze Songs fallen weg."""
    rated = sorted((rate(s, style, profile, settings, length) for s in songs if s.analysis.duration >= length),
                   key=lambda r: (-r.score, r.song.label.lower()))
    result: list[Suggestion] = []
    per_artist: dict[str, int] = {}
    limit = settings.suggest.max_per_artist
    for r in rated:
        artist = r.song.main_artist
        if artist and limit > 0 and per_artist.get(artist, 0) >= limit:
            continue
        per_artist[artist] = per_artist.get(artist, 0) + 1
        result.append(r)
        if len(result) >= top:
            break
    return result
