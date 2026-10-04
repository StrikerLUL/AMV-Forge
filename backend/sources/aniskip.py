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
) -> list[SkipTime]:
    """Gibt die OP/ED/Recap-Bereiche zurück, leere Liste wenn AniSkip nichts kennt.

    AniSkip-Zeiten stammen aus fremden Video-Dateien. Gibt es mehrere Einträge pro Typ,
    nehmen wir den, dessen Folgenlänge am nächsten an unserer Datei liegt.
    """
    params = [("types[]", t) for t in SKIP_TYPES] + [("episodeLength", "0")]
    status, body = client.get_json(f"/v2/skip-times/{mal_id}/{episode_number}", params=params)
    if status != 200 or not body or not body.get("found"):
        return []

    best: dict[str, dict] = {}
    for item in body.get("results", []):
        kind = item.get("skipType")
        if kind not in SKIP_TYPES:
            continue
        if kind not in best or (
            episode_duration is not None
            and abs(item["episodeLength"] - episode_duration)
            < abs(best[kind]["episodeLength"] - episode_duration)
        ):
            best[kind] = item

    result: list[SkipTime] = []
    for kind, item in best.items():
        start, end = float(item["interval"]["startTime"]), float(item["interval"]["endTime"])
        if episode_duration is not None and abs(item["episodeLength"] - episode_duration) > 10:
            log.warning(
                "AniSkip Folge %d: %s stammt aus einer Datei mit %.0f s, deine hat %.0f s (Zeiten evtl. verschoben)",
                episode_number, kind, item["episodeLength"], episode_duration,
            )
        result.append(SkipTime(kind=kind, start=start, end=end))
    return sorted(result, key=lambda s: s.start)
