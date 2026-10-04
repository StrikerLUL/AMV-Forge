# AMV-Forge

Aus einer Anime-Staffel und einem Song automatisch ein beat-synchrones 9:16-Edit für TikTok schneiden.
Die komplette Projektbeschreibung und Roadmap steht in [CLAUDE.md](CLAUDE.md).

## Stand: Phase 3 (Song-Struktur)

- **Phase 1, `quick`:** Eine Folge + ein Song → 9:16-MP4, jeder Schnitt exakt auf dem Beat.
- **Phase 2, `index`:** Eine ganze Staffel (aus einem Ordner oder aus Jellyfin) wird einmal analysiert und landet in `data/amv_forge.sqlite`: Folgen, Szenen ohne Opening/Ending/Recap, Metadaten und Charaktere von AniList, Filler-Markierung von Jikan. Ein zweiter Lauf berechnet nichts neu.
- **Phase 3, `song` und `edit`:** Der Song wird in Abschnitte (Intro, Verse, Chorus …) zerlegt, Drops werden erkannt. Im Verse wird ruhig geschnitten, im Build-up immer dichter, im Drop zweimal pro Beat. Jeder Clip wird so gelegt, dass sein stärkster Bewegungsmoment genau auf einem Beat liegt, und wilde Clips landen im Drop, ruhige im Intro. `edit` nimmt die Clips aus der ganzen Staffel.

### So läuft `quick` (Phase 1)

1. **Beats finden** (`backend/analysis/music/beats.py`): librosa schätzt das Tempo und die Zeitpunkte aller Beats im Song.
2. **Slots bauen** (`backend/planner/slots.py`): Ab dem ersten Beat wird alle `beats_per_cut` Beats geschnitten. Ein Slot ist die Zeit zwischen zwei Schnitten. Seit Phase 3 nur noch mit `--uniform`, sonst wie unten bei Phase 3.
3. **Szenen finden** (`backend/analysis/video/scenes.py`): PySceneDetect sucht alle Kamerawechsel. Das Ergebnis wird in `data/cache/scenes/` gespeichert.
4. **Clips verteilen** (`backend/planner/assign.py`): Jeder Slot bekommt eine Szene, die lang genug ist, möglichst ohne Wiederholung.
5. **Rendern** (`backend/render/ffmpeg_graph.py`): ffmpeg schneidet jeden Clip frame-genau auf 30 fps, macht daraus 1080×1920 (Mitte, Smart Reframe kommt in Phase 6) und legt den Song drunter.

### So läuft `index` (Phase 2)

1. **Folgen holen** (`backend/sources/folder.py`, `backend/sources/jellyfin.py`): Im Ordner-Modus wird die Folgennummer aus dem Dateinamen gelesen (`S01E03`, `01x03`, `- 03`, `Episode 3` …). Der Titel für AniList kommt aus dem Ordnernamen; heißt der Ordner nur `S1` oder `Season 1`, aus dem Ordner darüber. Bei Jellyfin werden die Folgen einmal nach `data/cache/episodes/` heruntergeladen.
2. **Metadaten** (`backend/sources/anilist.py`, `jikan.py`): AniList liefert MAL-ID, Genres, Tags und die Hauptfiguren mit Bild (für Phase 5). Passt kein Suchtreffer gut zum Titel, nimmt das Tool lieber gar keinen und zeigt die Kandidaten mit ID an. Für Staffel 2+ folgt das Tool den „Sequel“-Verknüpfungen ab Staffel 1. Jikan markiert Filler- und Recap-Folgen. Antwortet ein Dienst nicht, wird er nach einem zweiten Versuch für den Rest des Laufs übersprungen.
3. **OP/ED finden** (`backend/sources/aniskip.py`, `backend/analysis/audio/op_ed_detect.py`): AniSkip bekommt die echte Länge deiner Datei, Zeiten aus deutlich längeren oder kürzeren Fassungen werden verworfen. Zusätzlich vergleicht das Tool den Ton jeder Folge mit den Nachbarfolgen: Dieselbe Musik über 40–130 Sekunden ist das Opening bzw. Ending. Stimmen beide überein, wird beides zusammen rausgeschnitten; widersprechen sie sich, gewinnt der Audio-Vergleich, weil er auf deiner Datei gemessen ist.
4. **Szenen** wie in Phase 1, danach werden OP/ED/Recap herausgeschnitten (`backend/analysis/intervals.py`). Was übrig bleibt, sind die Clips in der Tabelle `clip`.
5. **Bewegung** (`backend/analysis/video/motion.py`, neu in Phase 3): siehe unten.
6. **Merken, was fertig ist** (`backend/indexer.py`): Jede Folge speichert, ob OP/ED, Szenen und Bewegung schon berechnet sind, und einen Fingerabdruck aus Dateigröße, Änderungszeit und Einstellungen. Ändert sich nichts, wird nichts neu gerechnet. Alle API-Antworten liegen in der Tabelle `apicache`.

### So laufen `song` und `edit` (Phase 3)

1. **Song analysieren** (`backend/analysis/music/structure.py`): Mit **allin1** (falls installiert, siehe unten) erkennt ein neuronales Netz Beats, Taktanfänge und Abschnitte. Ohne allin1 springt der **librosa-Fallback** ein:
   - `beats.py`: Beats mit librosa. Die „Eins“ im Takt wird geraten: Akkordwechsel und Bass-Schläge passieren fast immer dort.
   - `segments.py`: Für jeden Takt ein Klang-Fingerabdruck (Akkorde + Klangfarbe + Energie). Wo sich die Takte davor stark von denen danach unterscheiden, beginnt ein neuer Abschnitt. Der Name kommt aus der Energie: laut = Chorus, ruhig am Anfang/Ende = Intro/Outro.
2. **Energiekurve und Drops** (`energy.py`): Lautstärke, Anschläge und Bass ergeben eine Kurve von 0 (ruhig) bis 1 (volle Power). Ein Drop ist ein Taktanfang, an dem die Energie der 2 Takte danach deutlich über der der 2 Takte davor liegt und danach wirklich laut ist.
3. **Cache** (`backend/songs.py`): Das Ergebnis landet in der Tabelle `song`. Derselbe Song wird nie zweimal analysiert, außer die Datei oder die Einstellungen ändern sich (oder `--force`).
4. **Ausschnitt wählen** (`backend/planner/song_slots.py`): Ohne `--song-start` wird der Ausschnitt so gelegt, dass der stärkste Drop bei 40 % des Edits kommt, und er beginnt auf einem Taktanfang.
5. **Schnittpunkte**: Jeder Beat gehört zu einer Zone. Pro Zone steht in `cuts.beats_per_cut`, alle wie viele Beats geschnitten wird (Intro 4, Verse 2, Chorus 1). Die 4 Takte vor einem Drop sind der Build-up: dort werden die Schnitte Takt für Takt dichter (4 → 2 → 2 → 1 Beats). Ab dem Drop wird 4 Takte lang zweimal pro Beat geschnitten. Am Anfang jeder Zone wird immer geschnitten.
6. **Bewegung** (`backend/analysis/video/motion.py`): Optical Flow vergleicht zwei aufeinanderfolgende Bilder und schätzt, wie weit sich jeder Pixel bewegt hat. Das läuft auf einer 160×90-Mini-Version mit 12 Bildern pro Sekunde. Pro Clip werden die durchschnittliche Bewegung und der stärkste Moment (Peak) gespeichert. Die Kurve pro Folge liegt in `data/cache/motion/`.
7. **Clips verteilen** (`backend/planner/assign.py`): Jeder Slot hat eine Ziel-Intensität aus der Energiekurve. Ruhige Slots bekommen ruhige Clips, laute Slots wilde. Aus den 8 am besten passenden wird zufällig einer genommen, kein Clip doppelt, höchstens 2 Clips aus derselben Folge hintereinander. Dann wird der Clip so verschoben, dass sein Peak genau auf dem Schnitt liegt (oder auf einem anderen Beat im Slot, wenn er sonst nicht reinpasst).

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
Seit Phase 3 schneidet auch `quick` nach der Song-Struktur und misst beim ersten Mal die Bewegung der Folge (dauert etwa so lange wie die Szenensuche).

| Option | Bedeutung |
| --- | --- |
| `--length 30` | Länge des Edits in Sekunden |
| `--out pfad.mp4` | Ausgabedatei |
| `--song-start 45` | Ab Sekunde 45 im Song starten (wird auf den nächsten Beat gerundet). Ohne: der Drop kommt bei 40 % |
| `--uniform` | Gleichmäßig schneiden wie in Phase 1 (zum Vergleich), Rate aus `quick.beats_per_cut` |
| `--beats-per-cut 1` | Mit `--uniform`: 1 = jeder Beat, 2 = jeder zweite, 4 = jeder Takt |
| `--analyzer librosa` | Song-Analyse erzwingen: `auto` (Standard), `allin1` oder `librosa` |
| `--seed 123` | Gleicher Seed = gleiches Edit |
| `--preview` | Schnelle 480×854-Vorschau |
| `--no-music` | Ohne eingebrannte Musik, z. B. um auf TikTok den Sound aus der Bibliothek zu nehmen |
| `--config eigene.yaml` | Eigene Einstellungen statt `backend/config/default.yaml` |
| `-v` | Debug-Ausgaben |

Alle Standardwerte (Schnittraten, Szenen-Empfindlichkeit, Auflösung, Qualität) stehen in `backend/config/default.yaml`.
`quick` nutzt den OP/ED-Filter aus Phase 2 nicht. Dort kannst du mit `skip_start_seconds` und `skip_end_seconds` Anfang und Ende der Folge ausklammern, oder du nimmst gleich `edit`.

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

## Benutzung: Song und Edit aus der Staffel (Phase 3)

Song anschauen (Tempo, Abschnitte, Drops und wo ein 30-s-Edit starten würde):

```powershell
python -m backend.cli song "C:\Musik\song.mp3"
```

Staffel einmal neu indizieren, damit jeder Clip seine Bewegung bekommt (nur beim ersten Mal nach dem Update, OP/ED und Szenen werden nicht neu berechnet):

```powershell
python -m backend.cli index --source folder --path "P:\Anime\Horimiya\S1"
```

Dann das Edit (die DB-ID der Staffel zeigt `status`):

```powershell
python -m backend.cli edit --season 1 --song "C:\Musik\song.mp3" --length 30 --preview
```

Das Ergebnis landet in `data/renders/edit.mp4`, daneben `edit.plan.json` mit Abschnitt, Ziel-Intensität, Folge und Startzeit jedes Clips. In der Ausgabe steht pro Abschnitt, wie viele Clips es sind und wie lang sie im Schnitt sind. `edit` kennt dieselben Optionen wie `quick` (außer `--video`).

Die Schnittraten pro Abschnitt, Build-up und Drop stehen unter `cuts:` in `backend/config/default.yaml`, die Drop-Erkennung unter `music:`, die Bewegungsmessung unter `motion:` und die Clip-Auswahl unter `planner:`.

### Optional: allin1 (genauere Song-Analyse)

allin1 braucht PyTorch, madmom und NATTEN. NATTEN muss unter Windows selbst kompiliert werden und scheitert dort oft. Ohne allin1 läuft alles mit dem librosa-Fallback, das Tool sagt dir in der Ausgabe, welcher Analysator benutzt wurde.

Versuch es so (in der aktivierten venv):

```powershell
pip install torch
pip install git+https://github.com/CPJKU/madmom
pip install natten
pip install allin1
python -c "import allin1; print('allin1 ok')"
```

Klappt `pip install natten` nicht, nicht stundenlang daran festbeißen: Entweder beim librosa-Fallback bleiben oder AMV-Forge in WSL2 (Ubuntu unter Windows) laufen lassen, dort gibt es fertige NATTEN-Pakete. Nach einer erfolgreichen Installation einen schon analysierten Song mit `song "<pfad>" --analyzer allin1` neu analysieren. allin1 nutzt die NVIDIA-GPU, wenn PyTorch CUDA findet, das steht in der Ausgabe.

## Tests

```powershell
python -m pytest
```

## Rechtliches

Nur Folgen verwenden, die du legal besitzt. Anime-Ausschnitte und Songs sind urheberrechtlich geschützt: Für TikTok am besten mit `--no-music` exportieren und den Song über die TikTok-Soundbibliothek drunterlegen.
