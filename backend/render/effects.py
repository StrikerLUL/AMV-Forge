"""Übergänge und Effekte pro Clip (Phase 6), gesteuert von den Stil-Profilen in backend/styles/*.yaml.

Der Ablauf hat zwei Teile:
1. Vor der Clip-Wahl (prepare_slots): Slow-Mo und Speed-Ramps ändern, wie viel von einem Clip ein Slot
   braucht, und beim Freeze-Frame soll der Bewegungs-Peak lieber spät im Slot liegen.
2. Nach der Clip-Wahl (plan_effects): Welcher Übergang kommt an welchen Schnitt, wo ein Zoom-Punch, Shake,
   Freeze-Frame. Der Renderer (ffmpeg_graph.py) baut daraus die ffmpeg-Filter (die Funktionen unten).

Alle Zeiten sind Sekunden ab Slot-Anfang, außer es steht etwas anderes da.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace
from typing import Any, Sequence

from backend.config.settings import FxSettings
from backend.planner.assign import Assignment
from backend.planner.slots import Slot, Timing

TRANSITIONS = ("cut", "flash", "crossfade", "dip_white", "fade_black", "whip")
OVERLAP = ("crossfade", "whip")  # beide Clips sind gleichzeitig zu sehen (Überblendung)
DIPS = {"dip_white": "white", "fade_black": "black"}  # erst in die Farbe, dann aus ihr heraus
ZONES = ("intro", "verse", "chorus", "bridge", "outro", "buildup", "drop")
PUNCH_ON = ("cut", "downbeats", "beats")


@dataclass(frozen=True)
class TransitionRules:
    """Welcher Übergang an welchem Schnitt. None = wie default."""

    default: str = "cut"
    downbeat: str | None = None  # Schnitt auf einem Taktanfang
    section: str | None = None  # Wechsel des Abschnitts (Verse -> Chorus ...)
    drop: str | None = None  # erster Schnitt im Drop
    beats: float = 1.0  # Länge von crossfade, dip_white und fade_black in Beats

    def pick(self, drop: bool, section: bool, downbeat: bool) -> str:
        if drop and self.drop:
            return self.drop
        if section and self.section:
            return self.section
        if downbeat and self.downbeat:
            return self.downbeat
        return self.default


@dataclass(frozen=True)
class ZoneRule:
    """Ein Effekt, der nur in bestimmten Abschnitten gilt (leer = überall) und nur in Clips ab min_seconds."""

    zones: tuple[str, ...] = ()
    min_seconds: float = 0.0

    def applies(self, slot: Slot) -> bool:
        return (not self.zones or slot.section in self.zones) and slot.duration >= self.min_seconds - 1e-9


@dataclass(frozen=True)
class SlowmoRule(ZoneRule):
    speed: float = 0.8


@dataclass(frozen=True)
class RampRule(ZoneRule):
    slow: float = 0.5  # erst Zeitlupe (direkt nach dem Schnitt, wo der Bewegungs-Peak liegt) ...
    fast: float = 1.6  # ... dann schneller bis zum nächsten Schnitt
    split: float = 0.4  # Anteil des Slots in Zeitlupe


@dataclass(frozen=True)
class PunchRule(ZoneRule):
    zoom: float = 0.12  # 0.12 = kurz 12 % ranzoomen
    at: str = "cut"  # cut = am Schnitt, downbeats = auf jedem Taktanfang, beats = auf jedem Beat


@dataclass(frozen=True)
class ShakeRule(ZoneRule):
    strength: float = 0.02  # so weit wackelt das Bild (Anteil der Bildbreite)


@dataclass(frozen=True)
class PushRule(ZoneRule):
    zoom: float = 0.06  # über den ganzen Clip langsam so weit ranzoomen, auf das Gesicht zu


@dataclass(frozen=True)
class FreezeRule(ZoneRule):
    zoom: float = 0.2  # beim Einfrieren so weit auf das Gesicht zoomen


@dataclass(frozen=True)
class EffectRules:
    look: str | None = None  # Name aus fx.looks in default.yaml oder Pfad zu einer .cube-Datei
    glow: float = 0.0  # Soft Glow, 0-1
    vignette: float = 0.0  # dunkle Ränder, 0-1
    slowmo: SlowmoRule | None = None
    speed_ramp: RampRule | None = None
    zoom_punch: PunchRule | None = None
    shake: ShakeRule | None = None
    push_in: PushRule | None = None
    freeze: FreezeRule | None = None


NO_TRANSITIONS = TransitionRules()
NO_EFFECTS = EffectRules()
_RULES = {"slowmo": SlowmoRule, "speed_ramp": RampRule, "zoom_punch": PunchRule, "shake": ShakeRule,
          "push_in": PushRule, "freeze": FreezeRule}


def _check_transition(name: str, where: str) -> str:
    if name not in TRANSITIONS:
        raise ValueError(f"{where}: unbekannter Übergang '{name}' (erlaubt: {', '.join(TRANSITIONS)})")
    return name


def parse_transitions(raw: dict[str, Any] | None, where: str) -> TransitionRules:
    raw = dict(raw or {})
    allowed = {f.name for f in fields(TransitionRules)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{where}: unbekannter Eintrag unter transitions: {', '.join(unknown)}")
    rules = TransitionRules(
        default=_check_transition(str(raw.get("default", "cut")), where),
        **{key: _check_transition(str(raw[key]), where) if raw.get(key) else None
           for key in ("downbeat", "section", "drop")},
        beats=float(raw.get("beats", 1.0)),
    )
    if rules.beats <= 0:
        raise ValueError(f"{where}: transitions.beats muss größer als 0 sein")
    return rules


def _parse_rule(cls: type[ZoneRule], raw: dict[str, Any] | None, where: str) -> Any:
    if raw is None or raw is False:
        return None
    raw = {} if raw is True else dict(raw)
    allowed = {f.name for f in fields(cls)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{where}: unbekannte Einstellung {', '.join(unknown)} (erlaubt: {', '.join(sorted(allowed))})")
    zones = tuple(str(z) for z in raw.pop("zones", ()) or ())
    bad = sorted(set(zones) - set(ZONES))
    if bad:
        raise ValueError(f"{where}: unbekannter Abschnitt {', '.join(bad)} (erlaubt: {', '.join(ZONES)})")
    values = {k: (str(v) if isinstance(v, str) else float(v)) for k, v in raw.items()}
    rule = cls(zones=zones, **values)
    if isinstance(rule, PunchRule) and rule.at not in PUNCH_ON:
        raise ValueError(f"{where}: zoom_punch.at muss {', '.join(PUNCH_ON)} sein")
    if isinstance(rule, SlowmoRule) and not 0.1 <= rule.speed <= 4:
        raise ValueError(f"{where}: slowmo.speed muss zwischen 0.1 und 4 liegen")
    if isinstance(rule, RampRule) and (min(rule.slow, rule.fast) < 0.1 or not 0 < rule.split < 1):
        raise ValueError(f"{where}: speed_ramp braucht slow/fast ab 0.1 und split zwischen 0 und 1")
    return rule


def parse_effects(raw: dict[str, Any] | None, where: str) -> EffectRules:
    raw = dict(raw or {})
    allowed = {f.name for f in fields(EffectRules)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{where}: unbekannter Effekt {', '.join(unknown)} (erlaubt: {', '.join(sorted(allowed))})")
    look = raw.get("look")
    return EffectRules(
        look=str(look) if look else None,
        glow=float(raw.get("glow") or 0.0),
        vignette=float(raw.get("vignette") or 0.0),
        **{name: _parse_rule(cls, raw.get(name), f"{where} ({name})") for name, cls in _RULES.items()},
    )


# ---------------------------------------------------------------- vor der Clip-Wahl


def prepare_slots(slots: list[Slot], rules: EffectRules) -> list[Slot]:
    """Tempo pro Slot (Speed-Ramp vor Slow-Mo) und beim Freeze-Frame: Peak lieber auf einen späten Beat."""
    result = []
    for slot in slots:
        ramp, slow, freeze = rules.speed_ramp, rules.slowmo, rules.freeze
        if ramp is not None and ramp.applies(slot):
            slot = replace(slot, timing=Timing(((ramp.split, ramp.slow), (1.0, ramp.fast))))
        elif slow is not None and slow.applies(slot):
            slot = replace(slot, timing=Timing(((1.0, slow.speed),)))
        if freeze is not None and freeze.applies(slot) and len(slot.hits) > 1:
            # Der Clip soll erst laufen und dann auf dem Beat einfrieren (Pointe), also späte Beats zuerst
            late = sorted((h for h in slot.hits if h >= 0.3 * slot.duration), key=lambda h: abs(h - 0.6 * slot.duration))
            slot = replace(slot, hits=tuple(late) + tuple(h for h in slot.hits if h not in late))
        result.append(slot)
    return result


# ---------------------------------------------------------------- nach der Clip-Wahl


@dataclass(frozen=True)
class Transition:
    kind: str = "cut"
    frames: int = 0  # crossfade/whip: Überlappung pro Seite; dip_white/fade_black: Frames pro Seite; flash: Länge

    @property
    def overlap(self) -> int:
        """So viele Frames läuft der alte Clip nach dem Schnitt weiter (und der neue vorher schon)."""
        return self.frames if self.kind in OVERLAP else 0


CUT = Transition()


@dataclass(frozen=True)
class ClipFx:
    into: Transition = CUT  # Übergang am Anfang des Clips
    out: Transition = CUT  # Übergang am Ende (= into des nächsten Clips)
    punches: tuple[float, ...] = ()
    punch_zoom: float = 0.0
    shake: float = 0.0
    push_in: float = 0.0
    freeze_at: float | None = None
    freeze_zoom: float = 0.0
    speed: str = ""  # für Ausgabe und Schnittliste, z. B. "0.8x" oder "0.5x>1.6x"

    def labels(self) -> list[str]:
        """Kurze Namen der Effekte für Ausgabe und .plan.json."""
        names = []
        if self.speed:
            names.append(f"tempo {self.speed}")
        if self.punches and self.punch_zoom:
            names.append(f"zoom_punch x{len(self.punches)}")
        if self.shake:
            names.append("shake")
        if self.push_in:
            names.append("push_in")
        if self.freeze_at is not None:
            names.append("freeze")
        return names


NO_FX = ClipFx()


def _speed_label(timing: Timing) -> str:
    if timing.is_normal:
        return ""
    return ">".join(f"{speed:g}x" for _, speed in timing.pieces)


def _transition(kind: str, rules: TransitionRules, before: int, after: int, beat_seconds: float, fps: int,
                cfg: FxSettings) -> Transition:
    """Länge eines Übergangs in Frames, höchstens max_transition_share des kürzeren Clips."""
    limit = cfg.max_transition_share * min(before, after)
    if kind == "flash":
        frames = min(round(cfg.flash_seconds * fps), int(cfg.max_transition_share * after))
        return Transition(kind, frames) if frames >= 2 else CUT
    total = cfg.whip_seconds * fps if kind == "whip" else rules.beats * beat_seconds * fps
    per_side = int(min(total, limit) // 2)
    if kind == "cut" or per_side < 1:
        return CUT
    return Transition(kind, per_side)


def plan_effects(
    assignments: Sequence[Assignment],
    frames: Sequence[int],
    transitions: TransitionRules,
    effects: EffectRules,
    downbeats: Sequence[float],
    beat_seconds: float,
    fps: int,
    cfg: FxSettings,
) -> list[ClipFx]:
    """Übergang an jedem Schnitt und Effekte in jedem Clip. downbeats: Taktanfänge in Sekunden ab Edit-Anfang."""
    tol = 0.25 * beat_seconds

    def on_downbeat(t: float) -> bool:
        return any(abs(t - d) < tol for d in downbeats)

    into: list[Transition] = [CUT]
    for prev, cur, n_prev, n_cur in zip(assignments, assignments[1:], frames, frames[1:]):
        a, b = prev.slot, cur.slot
        kind = transitions.pick(drop=b.section == "drop" and a.section != "drop", section=b.section != a.section,
                                downbeat=on_downbeat(b.start))
        into.append(_transition(kind, transitions, n_prev, n_cur, beat_seconds, fps, cfg))

    result = []
    for i, a in enumerate(assignments):
        slot = a.slot
        fx = ClipFx(into=into[i], out=into[i + 1] if i + 1 < len(into) else CUT, speed=_speed_label(slot.timing))
        punch = effects.zoom_punch
        if punch is not None and punch.applies(slot):
            if punch.at == "cut":
                times = [0.0]
            elif punch.at == "beats":
                times = sorted(slot.hits)
            else:
                times = sorted(d - slot.start for d in downbeats if slot.start - tol <= d < slot.end - tol)
            fx = replace(fx, punches=tuple(max(0.0, round(t, 4)) for t in times), punch_zoom=punch.zoom)
        if effects.shake is not None and effects.shake.applies(slot):
            fx = replace(fx, shake=effects.shake.strength)
        if effects.push_in is not None and effects.push_in.applies(slot):
            fx = replace(fx, push_in=effects.push_in.zoom)
        freeze = effects.freeze
        if freeze is not None and freeze.applies(slot) and a.aligned and a.candidate and a.candidate.peak is not None:
            at = slot.timing.edit_offset(a.candidate.peak - a.source_start, slot.duration)
            if 0.25 * slot.duration <= at <= slot.duration - cfg.freeze_min_seconds:
                fx = replace(fx, freeze_at=round(at, 4), freeze_zoom=freeze.zoom)
        result.append(fx)
    return result


# ---------------------------------------------------------------- ffmpeg-Filter


def _num(value: float) -> str:
    return f"{value:.5f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def smoothstep(u: str) -> str:
    """Sanftes Anfahren und Abbremsen (0 -> 1) als ffmpeg-Ausdruck, u muss schon zwischen 0 und 1 liegen."""
    return f"({u})*({u})*(3-2*({u}))"


def zoom_filter(fx: ClipFx, focus: tuple[float, float], offset: float, duration: float, width: int, height: int,
                fps: int, cfg: FxSettings) -> str | None:
    """Zoom-Punch, Push-In und Freeze-Zoom in einem zoompan-Filter (Mitte = focus, z. B. das Gesicht).

    zoompan schneidet pro Bild einen kleineren Ausschnitt aus und skaliert ihn auf die volle Größe.
    offset: Sekunden ab Slot-Anfang, bei denen dieses Stück beginnt (on = Bildnummer im Stück).
    """
    t = f"(on/{fps}+{_num(offset)})"
    terms = []
    if fx.punches and fx.punch_zoom:
        expr = "0"
        for p in sorted(fx.punches):
            expr = f"if(gte({t},{_num(p)}),{_num(fx.punch_zoom)}*exp(-({t}-{_num(p)})/{_num(cfg.punch_seconds / 3)}),{expr})"
        terms.append(expr)
    if fx.push_in:
        terms.append(f"{_num(fx.push_in)}*clip({t}/{_num(max(duration, 1e-3))},0,1)")
    if fx.freeze_at is not None and fx.freeze_zoom:
        terms.append(f"{_num(fx.freeze_zoom)}*{smoothstep(f'clip(({t}-{_num(fx.freeze_at)})/0.3,0,1)')}")
    if not terms:
        return None
    fx_, fy = (min(max(v, 0.0), 1.0) for v in focus)
    z = "1+" + "+".join(terms)
    x = f"clip(iw*{_num(fx_)}-iw/zoom/2,0,iw-iw/zoom)"
    y = f"clip(ih*{_num(fy)}-ih/zoom/2,0,ih-ih/zoom)"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"


def shake_filter(strength: float, offset: float, width: int, height: int, cfg: FxSettings) -> str:
    """Shake: ein etwas kleinerer Ausschnitt, der hin und her wackelt (bewegter crop), wieder auf volle Größe."""
    s = min(max(strength, 0.0), 0.2)
    w = 2 * math.pi * cfg.shake_speed
    t = f"(t+{_num(offset)})"
    x = f"iw*{_num(s)}*(1+sin({t}*{_num(w)})*cos({t}*{_num(w * 0.37)}))"
    y = f"ih*{_num(s)}*(1+cos({t}*{_num(w * 0.83)})*sin({t}*{_num(w * 0.51)}+1))"
    return f"crop=w=iw*{_num(1 - 2 * s)}:h=ih*{_num(1 - 2 * s)}:x='{x}':y='{y}',scale={width}:{height},setsar=1"


def fade_filters(fx: ClipFx, start: int, end: int, frames: int, fps: int) -> list[str]:
    """Flash und Dips (Weiß, Schwarz) als fade-Filter. start/end: Frames ab Slot-Anfang, die dieses Stück zeigt."""
    result = []
    into, out = fx.into, fx.out
    if into.kind == "flash" or into.kind in DIPS:
        length = into.frames
        color = "white" if into.kind == "flash" else DIPS[into.kind]
        if start <= 0 < end and length > 0:
            result.append(f"fade=t=in:st={_num(-start / fps)}:d={_num(length / fps)}:color={color}")
    if out.kind in DIPS and out.frames > 0:
        begin = frames - out.frames
        if start <= begin < end:
            result.append(f"fade=t=out:st={_num((begin - start) / fps)}:d={_num(out.frames / fps)}:color={DIPS[out.kind]}")
    return result


def vignette_filter(strength: float) -> str:
    return f"vignette=angle={_num(min(max(strength, 0.0), 1.0) * math.pi / 2.5)}"
