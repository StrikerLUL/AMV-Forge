"""Phase 5 im ganzen Ablauf: Gesichter finden, Figuren zuordnen, 'characters' und 'edit --characters'.

Statt echter Anime-Gesichter malt das Testvideo farbige Quadrate (rot = Hori, blau = Miyamura,
grün = jemand ohne AniList-Eintrag). FakeFaceDetector findet die Quadrate, FakeClip sieht ihre Farbe.
Wie gut die Zuordnung selbst ist, prüft test_characters.py, hier geht es um den Ablauf.
"""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from sqlmodel import Session, select

from backend.cli import main
from backend.config import load_settings
from backend.config.settings import Settings
from backend.db import get_engine
from backend.db.models import Clip, Season
from backend.indexer import IndexReport, index_season, make_apis
from backend.analysis.loader import ModelLoader
from backend.sources.folder import scan_folder
from tests.fake_apis import FakeApis
from tests.fake_models import FakeClip, FakeFaceDetector
from tests.synth_video import FACE_COLORS, make_face_video, portrait_png

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")

SECONDS = 3.0
SCENES = [
    ["hori", "miyamura"], ["hori"], ["miyamura"], ["fremd"], ["hori", "miyamura"], [],
    ["miyamura", "hori"], ["hori"], ["miyamura"], ["hori", "miyamura"], [], ["hori", "miyamura"],
]
HORI, MIYAMURA = "1", "2"  # AniList-IDs aus fake_apis.py


def _expected(scene: list[str]) -> set[str]:
    return {{"hori": HORI, "miyamura": MIYAMURA}[who] for who in scene if who != "fremd"}


def _settings() -> Settings:
    settings = load_settings()
    return replace(settings, faces=replace(settings.faces, height=180),
                   characters=replace(settings.characters, download_interval=0.0))


def _index(folder: Path, settings: Settings, fake: FakeApis, detector: FakeFaceDetector) -> IndexReport:
    engine = get_engine(settings.database.path)
    models = ModelLoader(settings, clip=FakeClip(settings.clip), vad=None, dialog=None, faces=detector)
    return index_season(scan_folder(folder, settings.index.video_extensions), settings, engine,
                        make_apis(engine, settings.apis, fake.transport), models=models)


@pytest.fixture
def horimiya(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # Datenbank, Caches und Renders landen im Testordner
    folder = tmp_path / "Horimiya"
    folder.mkdir()
    make_face_video(folder / "Horimiya - 02.mkv", SCENES, SECONDS)  # Folge 2: AniSkip kennt kein OP/ED
    return folder


def _fake_apis() -> FakeApis:
    return FakeApis({"/h.png": portrait_png(FACE_COLORS["hori"]), "/m.png": portrait_png(FACE_COLORS["miyamura"])})


def test_index_finds_hori_and_miyamura_and_computes_nothing_twice(horimiya: Path) -> None:
    settings = _settings()
    fake, detector = _fake_apis(), FakeFaceDetector()
    first = _index(horimiya, settings, fake, detector)
    c = first.characters
    assert (c.faces, c.downloads, c.matched) == (1, 2, True)
    assert sorted(p.name for p in settings.characters.image_dir.glob("*.png")) == ["1.png", "2.png"]

    engine = get_engine(settings.database.path)
    with Session(engine) as session:
        assert session.exec(select(Season)).one().characters_signature
        clips = session.exec(select(Clip).order_by(Clip.start)).all()
        assert len(clips) == len(SCENES)
        for clip in clips:
            scene = SCENES[int(clip.start // SECONDS)]
            assert clip.faces is not None and len({tuple(f["box"]) for f in clip.faces}) == len(scene)
            for face in clip.faces:
                who = scene[0 if face["box"][0] < 0.4 else 1]  # links oder rechts im Bild
                if who != "fremd":  # mit nur zwei Figuren auf AniList landet ein Fremder oft bei einer davon
                    assert str(face["char"]) == {"hori": HORI, "miyamura": MIYAMURA}[who], (clip.start, face)
                    assert 0.6 <= face["p"] <= 1.0
            if "fremd" not in scene:
                assert set(clip.characters or {}) == _expected(scene), (clip.start, scene, clip.characters)

    fake.calls.clear()
    frames = detector.frames
    second = _index(horimiya, settings, fake, detector)
    assert not second.computed_anything and not second.characters.computed_anything
    assert fake.calls == [] and detector.frames == frames

    # Eigenes Vorbild dazu: nur neu zuordnen, keine Folge neu durchsuchen, nichts herunterladen
    own = settings.characters.extra_dir / "Hori"
    own.mkdir(parents=True)
    (own / "screenshot.png").write_bytes(portrait_png(FACE_COLORS["hori"]))
    third = _index(horimiya, settings, fake, detector)
    assert (third.characters.faces, third.characters.downloads, third.characters.matched) == (0, 0, True)
    assert detector.frames - frames == 3  # nur die drei Vorbilder (2x AniList, 1x eigenes), keine Folge


def test_characters_command_and_edit_with_both(horimiya: Path, synth_song: Path) -> None:
    settings = _settings()
    _index(horimiya, settings, _fake_apis(), FakeFaceDetector())
    with Session(get_engine(settings.database.path)) as session:  # der Fremde soll sicher "unbekannt" sein
        for clip in session.exec(select(Clip)):
            if SCENES[int(clip.start // SECONDS)] == ["fremd"]:
                clip.faces = [{**f, "char": None, "p": 0.0} for f in clip.faces or []]
                clip.characters = {}
                session.add(clip)
        session.commit()

    renders = Path("data") / "renders"
    assert main(["characters", "--season", "1", "--show", "Hori,Miyamura", "--top", "6"]) == 0
    for name in ("figur_s1_kyouko_hori.jpg", "figur_s1_izumi_miyamura.jpg", "figur_s1_unbekannt.jpg",
                 "figuren_s1_hori_miyamura.jpg"):
        assert (renders / name).is_file(), name
    assert main(["characters", "--season", "1", "--show", "Sakura"]) == 1  # unbekannter Name: klare Meldung

    out = renders / "paar.mp4"
    assert main(["edit", "--season", "1", "--song", str(synth_song), "--length", "8", "--preview", "--seed", "3",
                 "--characters", "Hori,Miyamura", "--analyzer", "librosa", "--out", str(out)]) == 0
    plan = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert plan["characters"] == ["Kyouko Hori", "Izumi Miyamura"]
    together = [c for c in plan["clips"] if {"Kyouko Hori", "Izumi Miyamura"} <= set(c["characters"] or {})]
    assert len(together) > len(plan["clips"]) / 2  # überwiegend beide im Bild
    pairs = sum(1 for scene in SCENES if _expected(scene) == {HORI, MIYAMURA})
    assert len({c["clip_id"] for c in together}) == pairs  # jede Szene mit beiden ist dabei
    assert all(c["characters"] for c in plan["clips"])  # keine Szene ganz ohne die beiden


def test_edit_with_characters_needs_the_phase_5_index(horimiya: Path, synth_song: Path) -> None:
    config = Path("light.yaml")
    config.write_text("faces:\n  enabled: false\nepisode_audio:\n  vad: none\nsubtitles:\n  model: \"\"\n"
                      "clip:\n  enabled: false\n", encoding="utf-8")
    assert main(["index", "--source", "folder", "--path", str(horimiya), "--no-api", "--config", str(config)]) == 0
    # ohne AniList keine Figuren: klare Meldung statt eines Edits ohne Paar
    assert main(["edit", "--season", "1", "--song", str(synth_song), "--characters", "Hori",
                 "--config", str(config)]) == 1
