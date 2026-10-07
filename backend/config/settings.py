"""Lädt die YAML-Einstellungen in typisierte Dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).with_name("default.yaml")
PROJECT_ROOT = DEFAULT_CONFIG.parent.parent.parent


def project_file(value: str) -> Path:
    """Dateien, die zum Projekt gehören (Prompts): relativ zum Arbeitsordner oder sonst zum Projektordner."""
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    return PROJECT_ROOT / path


@dataclass(frozen=True)
class QuickSettings:
    beats_per_cut: int
    min_slot_seconds: float
    skip_start_seconds: float
    skip_end_seconds: float


@dataclass(frozen=True)
class SceneSettings:
    adaptive_threshold: float
    min_scene_len_frames: int


@dataclass(frozen=True)
class RenderSettings:
    width: int
    height: int
    preview_width: int
    preview_height: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str


@dataclass(frozen=True)
class DatabaseSettings:
    path: Path


@dataclass(frozen=True)
class IndexSettings:
    source: str
    video_extensions: tuple[str, ...]
    download_dir: Path
    min_clip_seconds: float


@dataclass(frozen=True)
class ApiSettings:
    anilist_interval: float
    jikan_interval: float
    aniskip_interval: float
    timeout_seconds: float
    connect_timeout_seconds: float
    connection_retries: int
    max_retries: int
    anilist_min_title_score: float
    aniskip_max_length_diff: float


@dataclass(frozen=True)
class OpEdSettings:
    head_seconds: float
    tail_seconds: float
    min_seconds: float
    max_seconds: float
    similarity: float
    max_gap_seconds: float
    silence_db: float
    min_overlap: float


@dataclass(frozen=True)
class MusicSettings:
    analyzer: str
    sample_rate: int
    beats_per_bar: int
    allin1_work_dir: Path
    energy_rate: float
    energy_weights: dict[str, float]
    energy_smooth_seconds: float
    min_section_bars: int
    novelty_bars: int
    novelty_threshold: float
    chorus_energy: float
    calm_energy: float
    drop_window_bars: float
    drop_min_jump: float
    drop_min_level: float
    drop_min_distance_bars: float
    max_drops: int


@dataclass(frozen=True)
class CutSettings:
    beats_per_cut: dict[str, float]
    drop: float
    drop_bars: float
    buildup_bars: float
    buildup_from: float
    buildup_to: float
    min_cut_seconds: float
    drop_position: float


@dataclass(frozen=True)
class MotionSettings:
    fps: float
    width: int
    height: int
    edge_seconds: float
    peak_smooth: int
    cache_dir: Path
    hwaccel: str


@dataclass(frozen=True)
class ScoreWeights:
    """Gewichte der Score-Formel aus CLAUDE.md (w_m, w_e, w_c, w_q, w_r, w_d) plus w_o für den Folgen-Überhang."""

    mood: float = 0.0
    energy: float = 1.0
    character: float = 0.0
    quality: float = 0.3
    repeat: float = 0.3
    dialog: float = 0.0
    overuse: float = 0.0


@dataclass(frozen=True)
class PlannerSettings:
    pick_from_top: int
    max_same_episode_in_row: int
    max_same_character_in_row: int
    weights: ScoreWeights
    repeat_window: int
    spread_max_clips: int = 0
    spread_window_seconds: float = 60.0


@dataclass(frozen=True)
class KeyframeSettings:
    fps: float
    height: int
    max_per_clip: int
    seconds_per_frame: float
    edge_seconds: float
    cache_dir: Path
    jpeg_quality: int


@dataclass(frozen=True)
class ClipModelSettings:
    enabled: bool
    model: str
    pretrained: str
    device: str
    batch_size: int
    crop: str
    prompts: Path
    cache_dir: Path


@dataclass(frozen=True)
class EpisodeAudioSettings:
    sample_rate: int
    vad: str
    vad_threshold: float
    dialog_min_share: float


@dataclass(frozen=True)
class SubtitleSettings:
    enabled: bool
    languages: tuple[str, ...]
    skip_pattern: str
    min_overlap_seconds: float
    model: str
    device: str
    prompts: Path
    cache_dir: Path


@dataclass(frozen=True)
class MoodSettings:
    weights: dict[str, dict[str, float]]  # Signal -> Stimmung -> Gewicht


@dataclass(frozen=True)
class QualitySettings:
    min_score: float
    dark: float
    bright: float
    flat: float
    prompt_threshold: float
    blurry_ratio: float


@dataclass(frozen=True)
class FaceSettings:
    enabled: bool
    repo: str
    model: str
    device: str
    height: int
    min_confidence: float
    iou: float
    min_size: float
    max_per_frame: int
    crop_scale: float
    cache_dir: Path


@dataclass(frozen=True)
class CharacterSettings:
    image_dir: Path
    extra_dir: Path
    download_interval: float
    roles: tuple[str, ...]
    rounds: int
    seed_faces: int
    max_seed_faces: int
    seed_probability: float
    reference_weight: float
    scale: float
    min_probability: float


@dataclass(frozen=True)
class ReframeSettings:
    mode: str  # smart oder center
    margin: float
    min_face_share: float
    too_wide: str  # pan, main oder fit
    pan_min_seconds: float
    motion_min: float
    sheet: bool


@dataclass(frozen=True)
class FxSettings:
    flash_seconds: float
    whip_seconds: float
    whip_blur: float
    max_transition_share: float
    punch_seconds: float
    shake_speed: float
    freeze_min_seconds: float
    glow_blur: float
    looks: dict[str, dict[str, float]]  # Name -> Parameter (warmth, tint, saturation, contrast, brightness, lift)
    lut_size: int
    cache_dir: Path


@dataclass(frozen=True)
class Settings:
    quick: QuickSettings
    scenes: SceneSettings
    render: RenderSettings
    database: DatabaseSettings
    index: IndexSettings
    apis: ApiSettings
    op_ed: OpEdSettings
    music: MusicSettings
    cuts: CutSettings
    motion: MotionSettings
    planner: PlannerSettings
    keyframes: KeyframeSettings
    clip: ClipModelSettings
    episode_audio: EpisodeAudioSettings
    subtitles: SubtitleSettings
    mood: MoodSettings
    quality: QualitySettings
    faces: FaceSettings
    characters: CharacterSettings
    reframe: ReframeSettings
    fx: FxSettings


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Legt die eigenen Werte über die Standardwerte, auch in verschachtelten Abschnitten."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path: Path | None = None) -> Settings:
    """Liest backend/config/default.yaml. Eine eigene YAML muss nur die Werte enthalten, die sie ändert."""
    raw: dict[str, Any] = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    if path is not None:
        raw = _merge(raw, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    index = raw["index"]
    planner = raw["planner"]
    clip = raw["clip"]
    subs = raw["subtitles"]
    chars = raw["characters"]
    return Settings(
        quick=QuickSettings(**raw["quick"]),
        scenes=SceneSettings(**raw["scenes"]),
        render=RenderSettings(**raw["render"]),
        database=DatabaseSettings(path=Path(raw["database"]["path"])),
        index=IndexSettings(
            source=index["source"],
            video_extensions=tuple(e.lower() for e in index["video_extensions"]),
            download_dir=Path(index["download_dir"]),
            min_clip_seconds=float(index["min_clip_seconds"]),
        ),
        apis=ApiSettings(**raw["apis"]),
        op_ed=OpEdSettings(**raw["op_ed"]),
        music=MusicSettings(**{**raw["music"], "allin1_work_dir": Path(raw["music"]["allin1_work_dir"])}),
        cuts=CutSettings(**raw["cuts"]),
        motion=MotionSettings(**{**raw["motion"], "cache_dir": Path(raw["motion"]["cache_dir"])}),
        planner=PlannerSettings(**{**planner, "weights": ScoreWeights(**planner["weights"])}),
        keyframes=KeyframeSettings(**{**raw["keyframes"], "cache_dir": Path(raw["keyframes"]["cache_dir"])}),
        clip=ClipModelSettings(**{
            **clip, "prompts": project_file(clip["prompts"]), "cache_dir": Path(clip["cache_dir"]),
        }),
        episode_audio=EpisodeAudioSettings(**raw["episode_audio"]),
        subtitles=SubtitleSettings(**{
            **subs,
            "languages": tuple(str(lang).lower() for lang in subs["languages"]),
            "model": subs.get("model") or "",
            "prompts": project_file(subs["prompts"]),
            "cache_dir": Path(subs["cache_dir"]),
        }),
        mood=MoodSettings(weights={signal: dict(w) for signal, w in raw["mood"]["weights"].items()}),
        quality=QualitySettings(**raw["quality"]),
        faces=FaceSettings(**{**raw["faces"], "cache_dir": Path(raw["faces"]["cache_dir"])}),
        characters=CharacterSettings(**{
            **chars,
            "image_dir": Path(chars["image_dir"]),
            "extra_dir": Path(chars["extra_dir"]),
            "roles": tuple(str(r).upper() for r in chars["roles"]),
        }),
        reframe=_reframe(raw["reframe"]),
        fx=FxSettings(**{
            **raw["fx"],
            "looks": {str(name): {str(k): float(v) for k, v in (params or {}).items()}
                      for name, params in (raw["fx"]["looks"] or {}).items()},
            "cache_dir": Path(raw["fx"]["cache_dir"]),
        }),
    )


def _reframe(raw: dict[str, Any]) -> ReframeSettings:
    cfg = ReframeSettings(**raw)
    if cfg.mode not in ("smart", "center"):
        raise ValueError(f"reframe.mode muss smart oder center sein, ist {cfg.mode}")
    if cfg.too_wide not in ("pan", "main", "fit"):
        raise ValueError(f"reframe.too_wide muss pan, main oder fit sein, ist {cfg.too_wide}")
    return cfg
