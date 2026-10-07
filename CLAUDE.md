# AMV-Forge – Auto-AMV-Editor

Du baust zusammen mit Striker (Schüler/Azubi, kann Java, SQL, etwas Python und JavaScript) ein Tool, das aus einer ganzen Anime-Staffel und einem Song automatisch ein beat-synchrones 9:16-Edit für TikTok schneidet. Diese Datei ist die verbindliche Projektbeschreibung. Lies sie vor jeder Arbeit komplett.

## Ziel in einem Satz

Staffel + Stil-Wunsch (z. B. "Romance mit Paar A und B") rein → Tool analysiert alle Szenen, schlägt passende Songs vor, schneidet Clips exakt auf Beats und Drops → MP4 (1080×1920) plus DaVinci-Projektdatei raus.

## So arbeitest du in diesem Projekt

1. **Immer nur eine Phase.** Arbeite die Roadmap unten der Reihe nach ab. Fang nie mit einer späteren Phase an, bevor die aktuelle ihr "Fertig, wenn" erfüllt.
2. **Zu Beginn jeder Phase:** Kurz sagen, welche Dateien du anlegst oder änderst und welche Pakete neu dazukommen. Dann loslegen, nicht auf Freigabe warten, außer bei echten Unklarheiten.
3. **Am Ende jeder Phase:** Genaue Anleitung, wie Striker es testet (Befehle zum Kopieren, erwartetes Ergebnis), und die README aktualisieren.
4. **Vollständiger Code.** Keine Platzhalter wie `# ... rest bleibt gleich`, wenn Striker die Datei kopieren muss. Bei kleinen Änderungen genau sagen, welche Zeilen ersetzt werden.
5. **Erklären, aber kurz.** Bei neuen Konzepten (Optical Flow, CLIP, Embeddings) zwei, drei Sätze, was da passiert. Striker will verstehen, was er baut.
6. **Fehler ernst nehmen.** Wenn Striker einen Fehler schickt: Ursache erklären, dann Fix. Nicht raten, lieber nach der genauen Ausgabe fragen.
7. **Fortschritt festhalten.** Am Ende dieser Datei gibt es den Abschnitt "Stand". Schlag dort nach jeder Phase eine aktualisierte Version vor.

## Umgebung

- **Analyse-PC:** Windows mit Python 3.11, ffmpeg im PATH. GPU (NVIDIA/CUDA) nutzen, wenn vorhanden, sonst CPU-Fallback. Nie stillschweigend annehmen, dass CUDA da ist: `torch.cuda.is_available()` prüfen und loggen.
- **Jellyfin:** läuft auf Strikers Ionos-VPS (Ubuntu) hinter nginx. Zugriff nur über die Jellyfin-REST-API mit API-Key. Der VPS hat keine GPU, die Analyse läuft deshalb auf dem PC, nicht auf dem Server.
- **Videoquelle:** Beides unterstützen: `source: jellyfin` (Episoden über die API laden, lokal cachen) und `source: folder` (lokaler Ordner mit MKV/MP4).
- **Secrets:** Jellyfin-URL und API-Key nur in `.env` (mit `python-dotenv`), `.env` in `.gitignore`. Nie Keys in Code oder Beispielausgaben.
- **Repo:** GitHub-Account StrikerLUL, Repo-Name `amv-forge`.
- **Achtung Windows:** `allin1` braucht NATTEN, das unter Windows Probleme machen kann. Wenn die Installation scheitert: WSL2 vorschlagen oder auf den librosa-Fallback (siehe Phase 3) gehen. Nicht stundenlang an der Installation festbeißen.

## Tech-Stack

| Teil | Technik |
| --- | --- |
| Backend / API | Python 3.11, FastAPI |
| Szenen | PySceneDetect (AdaptiveDetector) |
| Bildinhalt | open_clip (CLIP), Zero-Shot mit Text-Prompts aus YAML |
| Bewegung | OpenCV Optical Flow (Farneback) |
| Ton der Folge | librosa (Lautheit), Silero VAD (Sprache) |
| Untertitel | pysubs2 (.ass/.srt), mehrsprachiger Emotions-Klassifikator |
| Gesichter | Anime-Face-Detektor + Embedding-Abgleich mit AniList-Charakterbildern |
| Musik | allin1 (BPM, Beats, Downbeats, Abschnitte), librosa (Energiekurve), Essentia (Stimmung, Tonart) |
| Datenbank | SQLite (SQLModel), Embeddings als .npy |
| Job-Queue | Huey oder RQ mit Redis (ab Phase 8) |
| Rendering | ffmpeg (filter_complex), OpenTimelineIO für DaVinci-Export |
| Frontend | React + Vite + Tailwind, Wavesurfer.js (ab Phase 8) |

Spotify-API **nicht** verwenden: Audio Features und Audio Analysis sind für neue Apps seit November 2024 gesperrt. Musikanalyse läuft immer lokal auf den Audiodateien.

## Externe APIs

| API | Wofür | Hinweise |
| --- | --- | --- |
| Jellyfin REST | Serien, Staffeln, Episoden, Untertitelspuren, Musikbibliothek | API-Key aus `.env` |
| AniList GraphQL | Genres, Tags (Romance usw.), Charaktere mit Bildern, Haupt-/Nebenrolle | Kein Key, Rate-Limit respektieren, Antworten cachen |
| Jikan (MyAnimeList) | MAL-ID, Episodentitel, Filler-Markierung | Kein Key, Rate-Limit respektieren, cachen |
| AniSkip | Opening/Ending/Recap-Zeitstempel pro Folge | Braucht MAL-ID, Daten oft lückenhaft → Fallback nötig |

Alle API-Antworten in SQLite cachen, damit eine Staffel nie zweimal abgefragt wird.

## Projektstruktur

```text
amv-forge/
  backend/
    api/            FastAPI-Routen (Staffel, Songs, Edits, Jobs)
    sources/        jellyfin.py, anilist.py, jikan.py, aniskip.py, folder.py
    analysis/
      video/        scenes.py, clip_tags.py, motion.py, faces.py, quality.py
      audio/        episode_audio.py, subtitles.py, op_ed_detect.py
      music/        structure.py, energy.py, mood.py
    planner/        slots.py, scoring.py, assign.py
    render/         ffmpeg_graph.py, reframe.py, effects.py, otio_export.py
    styles/         romance.yaml, hype.yaml, sad.yaml, funny.yaml, story.yaml
    prompts/        clip_prompts.yaml
    db/             models.py
    cli.py          Kommandozeile für Phase 1–7
  frontend/         React-App (ab Phase 8)
  data/             Cache, Datenbank, Renders (in .gitignore)
  tests/
  .env.example
  docker-compose.yml   (ab Phase 8, für Redis)
  README.md
  CLAUDE.md
```

Code-Regeln: Type Hints überall, kleine Module mit einer Aufgabe, Pfade über `pathlib`, Einstellungen in YAML statt hart codiert, Logging statt `print`, pytest-Tests für Planer und Scoring.

## Datenmodell (Kern)

- **Episode:** Serie, Staffel, Nummer, Dateipfad, MAL-ID, OP/ED-Zeitstempel, Filler ja/nein
- **Clip (Szene):** Episode, Start, Ende, Keyframe-Pfade, Bewegungswert, Bewegungs-Peak-Zeitpunkt, Lautheit, Dialog ja/nein, Untertiteltext, Stimmungsvektor (romance, action, sad, funny, calm je 0–1), Charaktere, Qualität, CLIP-Embedding
- **Song:** Pfad, BPM, Beats, Downbeats, Abschnitte (intro/verse/chorus/bridge/outro), Drop-Zeitpunkte, Energiekurve, Stimmung, Tonart
- **EditPlan:** Song, Stil, Slots (Start, Ende, Ziel-Intensität, gewählter Clip, Offset im Clip, Effekte)

## Wie die Erkennung funktioniert

Jeder Clip bekommt einmalig ein Profil, das gecacht wird:

- **Szenenwechsel** mit PySceneDetect (eine 24-Min-Folge ≈ 300–500 Clips).
- **CLIP-Zero-Shot** auf 2–3 Keyframes pro Clip gegen Prompts aus `prompts/clip_prompts.yaml`, z. B. "two anime characters blushing", "anime fight scene with explosions", "anime girl crying in the rain".
- **Optical Flow** für Action-Intensität und den Frame mit der stärksten Bewegung (Peak).
- **Ton der Folge:** laut/leise, Sprache ja/nein. Ruhige Szenen ohne Dialog bekommen Romance/Calm-Bonus.
- **Untertitel:** Emotionsklassifikation des Dialogs (Geständnis, Streit, Witz).
- **Charaktere:** Gesichter erkennen und mit AniList-Charakterbildern abgleichen, um "Paar A + B im Bild" zu finden.
- **Qualität:** unscharfe Schwenks, Schwarzbilder, Eyecatches, Credits rauswerfen.
- **OP/ED:** über AniSkip entfernen. Fallback: gleiches Audio an ähnlicher Stelle in mehreren Folgen per Audio-Fingerprint erkennen.

Daraus entsteht der Stimmungsvektor pro Clip. Die Gewichtung der Signale steht in YAML, damit sie ohne Codeänderung angepasst werden kann.

## Stil-Profile (YAML in `backend/styles/`)

| Stil | BPM | Schnittrate | Übergänge | Effekte | Bevorzugte Szenen |
| --- | --- | --- | --- | --- | --- |
| Romance | 70–100 | 1 Clip pro 2–4 Beats, länger im Refrain | Crossfade, Dip to White | Soft Glow, warme Farben, leichtes Slow-Mo | Blicke, Erröten, Paar im Bild, Sonnenuntergang |
| Hype | 140–175 | Build-up 1 pro Takt → 1 pro Beat; Drop 1 pro halben Beat | Harte Cuts, Flash, Whip | Zoom-Punch, Shake, Speed-Ramps | Hohe Bewegung, Explosionen |
| Sad | 60–90 | 1 Clip pro 4–8 Beats | Langsame Fades | Entsättigt, Vignette | Weinen, Abschied, Regen |
| Funny | 100–130 | Auf Pointen | Harte Cuts, Freeze-Frame | Zoom auf Gesicht | Übertriebene Reaktionen |
| Story | egal | Folgt den Song-Abschnitten | Gemischt | Wenig | Chronologisch, Plot-Momente |

Jedes Profil enthält: BPM-Bereich, Schnittrate pro Song-Abschnitt, erlaubte Übergänge, Effekte, Stimmungs-Zielvektor und die Gewichte für die Score-Formel.

## Schnitt-Planer

1. Song in Slots zerlegen (Schnittpunkte aus Beats/Downbeats/Abschnitten + Schnittrate des Stils). Jeder Slot hat eine Ziel-Intensität aus der Energiekurve.
2. Für jeden Slot alle Kandidaten-Clips bewerten:

   `Score = w_m·Stimmung + w_e·(1 − |I_Clip − I_Slot|) + w_c·Charakter + w_q·Qualität − w_r·Wiederholung − w_d·Dialogschnitt`

3. Bewegungs-Peak des Clips exakt auf den Beat legen (Offset im Clip berechnen).
4. Beste Action- bzw. Romance-Clips für Drop/Refrain reservieren.
5. Abwechslung: kein Clip doppelt, nicht 3× hintereinander dieselbe Folge oder Figur.

Start mit Greedy (Slot für Slot). Später optional globale Zuordnung (ungarischer Algorithmus, `scipy.optimize.linear_sum_assignment`).

## Rendering

- Smart Reframe 16:9 → 9:16: Ausschnitt folgt Gesichtern bzw. Bewegungsschwerpunkt, nicht stumpf die Mitte.
- Effekte über ffmpeg: Speed-Ramps (`setpts`), Zoom-Punch (`zoompan`), Flash (`eq`/`fade`), Shake (bewegter `crop`), Farblooks (`lut3d`).
- Erst 480p-Vorschau, final 1080×1920, H.264, AAC, 30 fps.
- Export der Timeline über OpenTimelineIO (FCPXML) für DaVinci Resolve.

## Roadmap

1. **Prototyp (1 Folge + 1 Song, CLI):** PySceneDetect + librosa-Beats + ffmpeg. Zufällige Clips, aber exakt auf Beats geschnitten.
   - Fertig, wenn: `python -m backend.cli quick --video folge.mkv --song song.mp3 --length 30` ein sauber im Takt geschnittenes 30-Sekunden-MP4 erzeugt.
2. **Staffel-Index + APIs:** ganze Staffel aus Jellyfin oder Ordner, OP/ED-Filter über AniSkip (+ Fallback), Metadaten von AniList/Jikan, alles in SQLite gecacht.
   - Fertig, wenn: eine 12-Folgen-Staffel einmal analysiert ist und ohne OP/ED in der Datenbank liegt; ein zweiter Lauf nichts neu berechnet.
3. **Song-Struktur:** allin1 für Abschnitte (Fallback librosa: Energie + Onset-Stärke), Drop-Erkennung, Bewegungs-Peaks auf Beats ausrichten.
   - Fertig, wenn: der Drop sichtbar anders geschnitten ist als der Verse.
4. **Stimmungserkennung:** CLIP, Bewegung, Episodenton, Untertitel → Stimmungsvektor.
   - Fertig, wenn: ein Romance-Edit fast nur ruhige Paar-Szenen enthält und ein Hype-Edit fast nur Action.
5. **Charaktere:** Gesichtserkennung, Abgleich mit AniList-Bildern, Filter "nur Paar A + B".
   - Fertig, wenn: `--characters "Hori,Miyamura"` überwiegend Szenen mit beiden liefert.
6. **Stil-Profile + Effekte + 9:16-Reframe.**
   - Fertig, wenn: dieselbe Staffel mit `--style romance` und `--style hype` klar unterschiedlich aussieht und Gesichter im 9:16-Bild bleiben.
7. **Songvorschläge:** Jellyfin-Musikbibliothek analysieren, Top 5 pro Staffel und Stil mit Begründung.
   - Fertig, wenn: `python -m backend.cli suggest --season <id> --style romance` fünf Songs mit kurzer Begründung ausgibt.
8. **Web-UI:** FastAPI-Endpunkte + React-App: Staffel wählen, Stil wählen, Timeline-Vorschau, Clips austauschen, Job-Status, DaVinci-Export.
9. **Extras:** Prompt-Modus per LLM ("Romance-Edit von X und Y, ruhiger Song, 30 Sekunden" → Konfiguration), eigene Stil-Profile, Batch-Modus.

## Grenzen

- Nur Folgen verwenden, die Striker legal besitzt. Das Tool lädt nichts von Piraterie-Seiten.
- Anime-Ausschnitte und Songs sind urheberrechtlich geschützt. Beim Upload auf TikTok besser die Musik über die TikTok-Soundbibliothek einfügen. Das Tool soll optional ein Edit **ohne** eingebrannte Musik exportieren können.

## Stand

- Phase: 5 (Charaktere) lief am 2026-10-07 auf Horimiya S1 (`P:\Anime\Horimiya\S1`) technisch durch: 10312 von 12157 Gesichtern einer Figur zugeordnet, Paar-Edit 71 von 81 Schnitten mit Hori und Miyamura. Strikers Urteil zu den Kontaktbögen und `paar.mp4` steht aus (auffällig: Takako Nishi und Iori Miyamura mit sehr vielen Clips). Danach Streuung gegen Szenen-Häufungen eingebaut, wartet auf Strikers Vergleich vorher/nachher (gleicher Seed). Phase 4 (romance und hype zahlenmäßig klar verschieden) ebenfalls von Striker noch nicht angeschaut. Danach Phase 6 (Stil-Profile, Effekte, 9:16-Reframe).
- Letzte Änderung: 2026-10-07, Streuung (`backend/planner/spread.py`): Aus jeder Spanne von 60 s einer Folge kommen höchstens 2 Clips ins Edit (`planner.spread_max_clips`, `planner.spread_window_seconds`), gibt es an anderen Stellen keinen passenden Clip mehr, steigt die Grenze um 1, statt Schnitte zu verlieren. Ein reiner Mindestabstand (30 s) kostete in der Simulation viel mehr Action als 2 pro Minute. Neuer Score-Term `overuse` (Gewicht 0.3, Stile erben ihn): Abzug, wenn eine Folge im bisherigen Edit deutlich öfter dran war als der Durchschnitt (Clips/Folgenzahl, voller Abzug ab doppelt so oft + 1). Vorrang bei Konflikten: gewünschte Figuren > Stil-Pool > Streuung > Folge/Figur nicht 3x hintereinander > Peak auf dem Beat. `edit` und `quick` loggen „Streuung … eingehalten bei X von Y Clips“ und „Dichteste Stelle“, die `.plan.json` hat pro Clip `crowd`. Mit `spread_max_clips: 0` und `weights.overuse: 0` (eigene YAML über `--config`) kommt bei gleichem Seed genau das alte Edit. Anlass: hype-Edit mit 9 Clips aus Folge 1 zwischen 10:18 und 10:50, Paar-Edit mit 20 von 81 Schnitten aus Folge 6. 186 Tests.
- Phase 2-5 im Überblick: `index` (Quelle `folder` oder `jellyfin`), `status`, `jellyfin-search`, `song`, `edit`, `quick`, `moods`. SQLite über SQLModel in `data/amv_forge.sqlite` (season, episode, skipsegment, clip, character, apicache, song). OP/ED aus AniSkip + Audio-Vergleich (auf Horimiya ~89 s pro OP/ED). Song-Analyse librosa-Fallback (allin1 optional), Schnittraten pro Abschnitt, Build-up und Drop aus YAML, Bewegungs-Peak auf dem Beat. Phase 4: Standbilder, CLIP-Zero-Shot (open_clip ViT-B-32, Prompts in `backend/prompts/clip_prompts.yaml`), Bildqualität, Lautstärke + Silero VAD, Untertitel mit Satz-Modell, Stimmungsvektor aus Signal-Rängen (`mood.weights`), Score-Formel in `backend/planner/scoring.py`, `edit --style`. Jede Folge merkt sich Signaturen pro Schritt, ein zweiter Lauf rechnet nichts neu, fehlende Pakete oder gescheiterte Downloads lassen nur das Signal weg. Phase 5: Anime-Gesichter mit YOLOv8 (`deepghs/anime_face_detection`, `face_detect_v1.4_s/model.onnx`, onnxruntime) in den Standbildern (480 px), Ausschnitt 1,8x mit CLIP als Embedding (`data/cache/faces/`), Vorbilder aus AniList (MAIN, SUPPORTING) plus `data/characters/<Name>/`, Zuordnung in `analysis/characters.py` (5 eindeutigste Gesichter, 3 Runden, Sicherheit = Abstand zur zweitähnlichsten Figur, `characters.min_probability`), `edit --characters` (erst alle Figuren, dann eine, Stil-Pool unter diesen Clips, Score-Term `character`, keine Figur öfter als 2x hintereinander), Befehl `characters` mit Kontaktbögen.
- Abweichungen von der Struktur: Beats in `backend/analysis/music/beats.py`, librosa-Abschnitte in `music/segments.py`, allin1 in `music/allin1_backend.py`, Song-Cache in `backend/songs.py`, Schnittpunkte nach Song-Struktur in `backend/planner/song_slots.py`, Einstellungen und Stil-Lader in `backend/config/`, ffmpeg-Helfer in `backend/media.py`, Index-Ablauf in `backend/indexer.py` (Phase 2/3), `backend/mood_index.py` (Phase 4) und `backend/character_index.py` (Phase 5), Modell-Lader in `analysis/loader.py`, Signaturen in `backend/signatures.py`, Standbilder in `analysis/video/keyframes.py`, Stimmungsvektor in `analysis/mood.py`, Figuren-Zuordnung in `analysis/characters.py`, Namenssuche in `analysis/character_names.py`, Figurenbilder in `sources/character_images.py`, GPU-Wahl in `analysis/device.py`, Kontaktbögen in `render/contact_sheet.py`, Streuung in `planner/spread.py`, CLI-Befehle in `backend/commands/` (season.py, song.py, edit.py, moods.py, characters.py). Statt "Keyframe-Pfade" speichert der Clip ein Vorschaubild (`thumbnail`). Gesichter werden mit CLIP statt mit einem eigenen Gesichts-Embedding-Modell verglichen. Die Score-Formel hat zusätzlich `− w_o·Überhang` (`weights.overuse`). Die Stil-YAMLs enthalten bis Phase 6 nur `mood`, `pool` und `weights`, `story.yaml` kommt in Phase 6.
- Offene Fragen: Streuung nur an einer simulierten Staffel geprüft (hype: dichteste Stelle 5-6 → 2 Clips pro Minute, Action-Mittel 0.94 → 0.87). Beim Paar-Edit mit 81 Schnitten gehen alle 71 Paar-Clips ins Edit, dort ändert die Streuung kaum etwas, erst wenn es mehr passende Clips als Schnitte gibt (z. B. `--length 15` oder langsamere Schnitte in Phase 6). Gesichtserkennung und Zuordnung nur mit künstlichen Daten getestet (in der Cloud-Umgebung gibt es keine Anime-Bilder und keinen Hugging-Face-Zugang), deshalb sind die `characters`-Kontaktbögen von Horimiya der eigentliche Test. Ob das YOLO-Modell unter diesem Pfad auf Hugging Face liegt, ist von hier nicht prüfbar (sonst `faces.model` in YAML anpassen). CLIP-Prompts ebenso nur künstlich geprüft. Satz-Modell für Untertitel nur mit Ersatz-Modell getestet, ob Strikers MP4s Untertitelspuren haben, ist unklar. allin1 unter Windows nicht getestet (NATTEN). Jikan war von Strikers PC aus nicht erreichbar (WinError 10060), deshalb keine Filler-Infos. Jellyfin-Quelle noch nicht getestet. Strikers PC hat Python 3.10.11, der Code läuft auf 3.10 und 3.11.
