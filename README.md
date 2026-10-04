# AMV-Forge

Aus einer Anime-Staffel und einem Song automatisch ein beat-synchrones 9:16-Edit für TikTok schneiden.
Die komplette Projektbeschreibung und Roadmap steht in [CLAUDE.md](CLAUDE.md).

## Stand: Phase 1 (Prototyp)

Eine Folge + ein Song rein, ein 9:16-MP4 raus. Die Clips sind noch zufällig, aber jeder Schnitt liegt exakt auf einem Beat.

So läuft es ab:

1. **Beats finden** (`backend/analysis/music/beats.py`): librosa schätzt das Tempo und die Zeitpunkte aller Beats im Song.
2. **Slots bauen** (`backend/planner/slots.py`): Ab dem ersten Beat wird alle `beats_per_cut` Beats geschnitten. Ein Slot ist die Zeit zwischen zwei Schnitten.
3. **Szenen finden** (`backend/analysis/video/scenes.py`): PySceneDetect sucht alle Kamerawechsel in der Folge. Das Ergebnis wird in `data/cache/scenes/` gespeichert, der zweite Lauf mit derselben Folge ist sofort fertig.
4. **Clips verteilen** (`backend/planner/assign.py`): Jeder Slot bekommt eine zufällige Szene, die lang genug ist, möglichst ohne Wiederholung. So gibt es keinen ungewollten Schnitt mitten im Slot.
5. **Rendern** (`backend/render/ffmpeg_graph.py`): ffmpeg schneidet jeden Clip frame-genau auf 30 fps, macht daraus 1080×1920 (Mitte, Smart Reframe kommt in Phase 6) und legt den Song drunter.

## Installation (Windows)

Voraussetzungen: Python 3.11 und ffmpeg im PATH (`ffmpeg -version` muss funktionieren).

```powershell
git clone https://github.com/StrikerLUL/amv-forge.git
cd amv-forge
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Benutzung

```powershell
python -m backend.cli quick --video folge.mkv --song song.mp3 --length 30
```

Das Ergebnis landet in `data/renders/quick.mp4`, daneben `quick.plan.json` mit der Schnittliste.

| Option | Bedeutung |
| --- | --- |
| `--length 30` | Länge des Edits in Sekunden |
| `--out pfad.mp4` | Ausgabedatei |
| `--beats-per-cut 1` | Schnittrate: 1 = jeder Beat, 2 = jeder zweite (Standard), 4 = jeder Takt |
| `--song-start 45` | Ab Sekunde 45 im Song starten (wird auf den nächsten Beat gerundet) |
| `--seed 123` | Gleicher Seed = gleiches Edit |
| `--preview` | Schnelle 480×854-Vorschau |
| `--no-music` | Ohne eingebrannte Musik, z. B. um auf TikTok den Sound aus der Bibliothek zu nehmen |
| `--config eigene.yaml` | Eigene Einstellungen statt `backend/config/default.yaml` |
| `-v` | Debug-Ausgaben |

Alle Standardwerte (Schnittrate, Szenen-Empfindlichkeit, Auflösung, Qualität) stehen in `backend/config/default.yaml`.
Solange es noch keinen OP/ED-Filter gibt (Phase 2), kannst du dort mit `skip_start_seconds` und `skip_end_seconds` Anfang und Ende der Folge ausklammern.

## Tests

```powershell
python -m pytest
```

## Rechtliches

Nur Folgen verwenden, die du legal besitzt. Anime-Ausschnitte und Songs sind urheberrechtlich geschützt: Für TikTok am besten mit `--no-music` exportieren und den Song über die TikTok-Soundbibliothek drunterlegen.
