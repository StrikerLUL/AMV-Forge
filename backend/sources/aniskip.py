"""AniSkip: Zeitstempel für Opening, Ending und Recap einer Folge."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.sources.http import ApiClient

log = logging.getLogger(__name__)

SKIP_TYPES = ("op", "ed", "recap", "mixed-op", "mixed-ed")


@dataclass(frozen=True)
class SkipTime:
    kind: str
    start: float
    end: float


def skip_times(
    client: ApiClient,
    mal_id: int,
    episode_number: int,
    episode_duration: float | None = None,
    max_length_diff: float = 15.0,
) -> list[SkipTime]:
    """Gibt die OP/ED/Recap-Bereiche zurück, leere Liste wenn AniSkip nichts Passendes kennt.

    AniSkip-Zeiten stammen aus fremden Video-Dateien. Ist deren Länge zu weit von unserer Datei
    entfernt, sind die Zeiten vermutlich verschoben (andere Fassung, Intro-Logo, falsches Anime)
    und werden verworfen. Bei mehreren Einträgen pro Typ gewinnt die passendste Länge.
    """
    length = str(round(episode_duration)) if episode_duration else "0"
    params = [("types[]", t) for t in SKIP_TYPES] + [("episodeLength", length)]
    status, body = client.get_json(f"/v2/skip-times/{mal_id}/{episode_number}", params=params)
    if status != 200 or not body or not body.get("found"):
        return []

    best: dict[str, dict] = {}
    for item in body.get("results", []):
        kind = item.get("skipType")
        if kind not in SKIP_TYPES:
            continue
        diff = abs(item["episodeLength"] - episode_duration) if episode_duration else 0.0
        if diff > max_length_diff:
            log.warning(
                "AniSkip Folge %d: %s verworfen, stammt aus einer Datei mit %.0f s, deine hat %.0f s",
                episode_number, kind, item["episodeLength"], episode_duration,
            )
            continue
        if kind not in best or diff < abs(best[kind]["episodeLength"] - (episode_duration or 0.0)):
            best[kind] = item

    result = [
        SkipTime(kind=kind, start=float(item["interval"]["startTime"]), end=float(item["interval"]["endTime"]))
        for kind, item in best.items()
    ]
    return sorted(result, key=lambda s: s.start)
