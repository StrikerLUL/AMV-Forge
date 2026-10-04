# AMV-Forge

Aus einer Anime-Staffel und einem Song automatisch ein beat-synchrones 9:16-Edit für TikTok schneiden.
Die komplette Projektbeschreibung und Roadmap steht in [CLAUDE.md](CLAUDE.md).

## Stand: Phase 2 (Staffel-Index + APIs)

- **Phase 1, `quick`:** Eine Folge + ein Song → 9:16-MP4, zufällige Clips, aber jeder Schnitt exakt auf dem Beat.
- **Phase 2, `index`:** Eine ganze Staffel (aus einem Ordner oder aus Jellyfin) wird einmal analysiert und landet in `data/amv_forge.sqlite`: Folgen, Szenen ohne Opening/Ending/Recap, Metadaten und Charaktere von AniList, Filler-Markierung von Jikan. Ein zweiter Lauf berechnet nichts neu.

### So läuft `quick` (Phase 1)

1. **Beats finden** (`backend/analysis/music/beats.py`): librosa schätzt das Tempo und die Zeitpunkte aller Beats im Song.
2. **Slots bauen** (`backend/planner/slots.py`): Ab dem ersten Beat wird alle `beats_per_cut` Beats geschnitten. Ein Slot ist die Zeit zwischen zwei Schnitten.
3. **Szenen finden** (`backend/analysis/video/scenes.py`): PySceneDetect sucht alle Kamerawechsel. Das Ergebnis wird in `data/cache/scenes/` gespeichert.
4. **Clips verteilen** (`backend/planner/assign.py`): Jeder Slot bekommt eine zufällige Szene, die lang genug ist, möglichst ohne Wiederholung.
5. **Rendern** (`backend/render/ffmpeg_graph.py`): ffmpeg schneidet jeden Clip frame-genau auf 30 fps, macht daraus 1080×1920 (Mitte, Smart Reframe kommt in Phase 6) und legt den Song drunter.

### So läuft `index` (Phase 2)

1. **Folgen holen** (`backend/sources/folder.py`, `backend/sources/jellyfin.py`): Im Ordner-Modus wird die Folgennummer aus dem Dateinamen gelesen (`S01E03`, `01x03`, `- 03`, `Episode 3` …). Der Titel für AniList kommt aus dem Ordnernamen; heißt der Ordner nur `S1` oder `Season 1`, aus dem Ordner darüber. Bei Jellyfin werden die Folgen einmal nach `data/cache/episodes/` heruntergeladen.
2. **Metadaten** (`backend/sources/anilist.py`, `jikan.py`): AniList liefert MAL-ID, Genres, Tags und die Hauptfiguren mit Bild (für Phase 5). Passt kein Suchtreffer gut zum Titel, nimmt das Tool lieber gar keinen und zeigt die Kandidaten mit ID an. Für Staffel 2+ folgt das Tool den „Sequel“-Verknüpfungen ab Staffel 1. Jikan markiert Filler- und Recap-Folgen. Antwortet ein Dienst nicht, wird er nach einem zweiten Versuch für den Rest des Laufs übersprungen.
3. **OP/ED finden** (`backend/sources/aniskip.py`, `backend/analysis/audio/op_ed_detect.py`): AniSkip bekommt die echte Länge deiner Datei, Zeiten aus deutlich längeren oder kürzeren Fassungen werden verworfen. Zusätzlich vergleicht das Tool den Ton jeder Folge mit den Nachbarfolgen: Dieselbe Musik über 40–130 Sekunden ist das Opening bzw. Ending. Stimmen beide überein, wird beides zusammen rausgeschnitten; widersprechen sie sich, gewinnt der Audio-Vergleich, weil er auf deiner Datei gemessen ist.
4. **Szenen** wie in Phase 1, danach werden OP/ED/Recap herausgeschnitten (`backend/analysis/intervals.py`). Was übrig bleibt, sind die Clips in der Tabelle `clip`.
5. **Merken, was fertig ist** (`backend/indexer.py`): Jede Folge speichert, ob OP/ED und Szenen schon berechnet sind, und einen Fingerabdruck aus Dateigröße, Änderungszeit und Einstellungen. Ändert sich nichts, wird nichts neu gerechnet. Alle API-Antworten liegen in der Tabelle `apicache`.

## Installation (Windows)

Voraussetzungen: Python 3.10 oder 3.11 und ffmpeg im PATH (`ffmpeg -version` muss funktionieren).

```powershell
git clone https://github.com/StrikerLUL/amv-forge.git
cd amv-forge
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Benutzung: eine Folge (Phase 1)

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
`quick` nutzt den OP/ED-Filter aus Phase 2 noch nicht. Dort kannst du mit `skip_start_seconds` und `skip_end_seconds` Anfang und Ende der Folge ausklammern.

## Benutzung: ganze Staffel (Phase 2)

Aus einem Ordner (Folgennummer kommt aus dem Dateinamen, Titel aus dem Ordnernamen bzw. dem Ordner darüber):

```powershell
python -m backend.cli index --source folder --path "P:\Anime\Horimiya\S1"
```

Aus Jellyfin (vorher `.env` aus `.env.example` anlegen):

```powershell
python -m backend.cli jellyfin-search "Horimiya"
python -m backend.cli index --source jellyfin --season <ID aus der Liste>
```

Danach anschauen, was in der Datenbank liegt:

```powershell
python -m backend.cli status
python -m backend.cli status --season 1
```

| Option für `index` | Bedeutung |
| --- | --- |
| `--title "Horimiya"` | Suchbegriff für AniList, falls der Ordnername komisch ist |
| `--season-number 2` | Staffelnummer, falls nicht im Namen |
| `--anilist-id 124080` | AniList-ID direkt setzen, wenn die Suche das falsche Anime findet (Zahl aus der AniList-URL) |
| `--no-api` | Ohne Internet: keine Metadaten, OP/ED nur per Audio-Vergleich |
| `--force` | Alles neu berechnen |

Die Einstellungen für OP/ED-Erkennung, Rate-Limits, Timeouts und Download-Ordner stehen in `backend/config/default.yaml`. Eine eigene YAML für `--config` muss nur die Werte enthalten, die du ändern willst.
Tipp: Liegt das Repo in OneDrive, setz `index.download_dir` auf einen Ordner außerhalb, sonst lädt OneDrive die heruntergeladenen Folgen mit hoch.

## Tests

```powershell
python -m pytest
```

## Rechtliches

Nur Folgen verwenden, die du legal besitzt. Anime-Ausschnitte und Songs sind urheberrechtlich geschützt: Für TikTok am besten mit `--no-music` exportieren und den Song über die TikTok-Soundbibliothek drunterlegen.
