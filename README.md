# AMV-Forge

Aus einer Anime-Staffel und einem Song automatisch ein beat-synchrones 9:16-Edit für TikTok schneiden.
Die komplette Projektbeschreibung und Roadmap steht in [CLAUDE.md](CLAUDE.md).

## Stand: Phase 7 (Songvorschläge)

- **Phase 1, `quick`:** Eine Folge + ein Song → 9:16-MP4, jeder Schnitt exakt auf dem Beat.
- **Phase 2, `index`:** Eine ganze Staffel (aus einem Ordner oder aus Jellyfin) wird einmal analysiert und landet in `data/amv_forge.sqlite`: Folgen, Szenen ohne Opening/Ending/Recap, Metadaten und Charaktere von AniList, Filler-Markierung von Jikan. Ein zweiter Lauf berechnet nichts neu.
- **Phase 3, `song` und `edit`:** Der Song wird in Abschnitte (Intro, Verse, Chorus …) zerlegt, Drops werden erkannt. Im Verse wird ruhig geschnitten, im Build-up immer dichter, im Drop zweimal pro Beat. Jeder Clip wird so gelegt, dass sein stärkster Bewegungsmoment genau auf einem Beat liegt, und wilde Clips landen im Drop, ruhige im Intro. `edit` nimmt die Clips aus der ganzen Staffel.
- **Phase 4, Stimmung und `--style`:** `index` schaut sich jetzt jeden Clip an (CLIP), hört hin (Lautstärke, wird geredet?) und liest die Untertitel. Daraus wird pro Clip ein Stimmungsvektor (romance, action, sad, funny, calm). `edit --style romance` nimmt dann fast nur ruhige Paar-Szenen, `--style hype` fast nur Action. Schwarzbilder, Abspann, Logos und verwackelte Clips fliegen raus. `moods` zeigt, was erkannt wurde, mit Kontaktbögen zum Anschauen.
- **Phase 5, Figuren und `--characters`:** `index` sucht jetzt in jedem Clip Anime-Gesichter und ordnet sie den Figuren von AniList zu. `edit --characters "Hori,Miyamura"` nimmt dann zuerst Szenen, in denen beide zu sehen sind. `characters` zeigt, wer wie oft erkannt wurde, mit Kontaktbögen der Gesichter zum Prüfen.
- **Streuung (nach Phase 5):** Aus jeder Minute einer Folge kommen höchstens 2 Clips ins Edit, und Folgen, die schon deutlich öfter dran waren als der Durchschnitt, bekommen einen Abzug. Ein Edit erzählt so nicht mehr eine einzelne Kampfszene nach.
- **Phase 6, Stil-Profile, Effekte und Smart Reframe:** Jeder Stil (`romance`, `hype`, `sad`, `funny`, neu `story`) bringt jetzt sein eigenes Tempo, seine Schnittrate, Übergänge (Crossfade, Dip to White, Flash, Whip …), Effekte (Slow-Mo, Speed-Ramps, Zoom-Punch, Shake, Freeze-Frame) und einen Farblook mit. Der 9:16-Ausschnitt folgt den Gesichtern aus Phase 5 bzw. der Bewegung, statt stumpf die Mitte zu nehmen. Ein Kontaktbogen zeigt pro Clip, wo der Ausschnitt liegt.
- **Phase 7, `music` und `suggest`:** Die eigene Musik (ein Ordner oder die Jellyfin-Musikbibliothek) wird einmal analysiert: Tempo, Abschnitte und Drops wie in Phase 3, dazu Stimmung (romance, action, sad, funny, calm, wie bei den Clips) und Tonart. `suggest --season 1 --style romance` zeigt dann die 5 Songs, die am besten zu den Romance-Szenen der Staffel passen, jeweils mit Begründung, Einwänden und dem fertigen `edit`-Befehl zum Kopieren.

### So läuft `quick` (Phase 1)

1. **Beats finden** (`backend/analysis/music/beats.py`): librosa schätzt das Tempo und die Zeitpunkte aller Beats im Song.
2. **Slots bauen** (`backend/planner/slots.py`): Ab dem ersten Beat wird alle `beats_per_cut` Beats geschnitten. Ein Slot ist die Zeit zwischen zwei Schnitten. Seit Phase 3 nur noch mit `--uniform`, sonst wie unten bei Phase 3.
3. **Szenen finden** (`backend/analysis/video/scenes.py`): PySceneDetect sucht alle Kamerawechsel. Das Ergebnis wird in `data/cache/scenes/` gespeichert.
4. **Clips verteilen** (`backend/planner/assign.py`): Jeder Slot bekommt eine Szene, die lang genug ist, möglichst ohne Wiederholung.
5. **Rendern** (`backend/render/ffmpeg_graph.py`): ffmpeg schneidet jeden Clip frame-genau auf 30 fps, macht daraus 1080×1920 (seit Phase 6 mit Smart Reframe, siehe unten) und legt den Song drunter.

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

### So läuft die Stimmungserkennung (Phase 4)

Alles passiert in `index`, pro Folge einmal (`backend/mood_index.py`):

1. **Standbilder** (`backend/analysis/video/keyframes.py`): ffmpeg tastet die Folge mit 4 Bildern pro Sekunde in 224 Pixel Höhe ab. Von jedem Clip werden 1–3 Bilder behalten, mit Abstand zum Schnitt. Das mittlere landet als Vorschaubild in `data/cache/keyframes/`.
2. **CLIP** (`backend/analysis/video/clip_tags.py`): CLIP rechnet Bilder und Sätze in denselben Zahlenraum um (ein *Embedding*, 512 Zahlen). Bild und passende Beschreibung liegen dort nah beieinander. Jedes Standbild wird mit den Sätzen aus `backend/prompts/clip_prompts.yaml` verglichen („an anime couple holding hands“, „anime characters fighting“ …). Weil CLIP nur Quadrate sieht, wird das 16:9-Bild in eine linke und rechte Hälfte geteilt, damit niemand am Rand abgeschnitten wird. Die Embeddings liegen in `data/cache/clip/`, neue Prompts brauchen deshalb kein neues Dekodieren.
3. **Bildqualität** (`quality.py`): Helligkeit, Kontrast und Schärfe kommen direkt aus den Pixeln, Text/Abspann/Logo erkennt CLIP. Schwarzbilder, Weißblitze, einfarbige Flächen, Texttafeln und sehr unscharfe Clips kommen nie ins Edit.
4. **Ton der Folge** (`backend/analysis/audio/episode_audio.py`): Lautstärke pro Clip in dB. **Silero VAD** (Voice Activity Detection) ist ein kleines neuronales Netz, das in 32-ms-Stücken entscheidet, ob eine Stimme zu hören ist. So weiß jeder Clip, wie viel darin geredet wird.
5. **Untertitel** (`subtitles.py`): Datei neben der Folge (`Folge.de.ass`, `Folge.srt`) oder Spur in der MKV/MP4. Schilder, Liedtexte und Geräusch-Beschreibungen fliegen raus. Ein mehrsprachiges Satz-Modell vergleicht jede Zeile mit den Beispielen in `backend/prompts/dialog_prompts.yaml` (Geständnis, Streit, Witz, Trauer). „Ich liebe dich“ und „I love you“ landen dabei fast am selben Punkt.
6. **Stimmungsvektor** (`backend/analysis/mood.py`): Jedes Signal wird innerhalb der Staffel in einen Rang 0–1 umgerechnet (0 = kleinster Wert der Staffel, 1 = größter). Pro Stimmung ergibt sich dann ein gewichteter Mittelwert: viel Bewegung und laut → action, leise ohne Sprache → romance/calm, dazu CLIP und Untertitel. Die Gewichte stehen unter `mood.weights` in `default.yaml`. Fehlt ein Signal (keine Untertitel), zählen nur die anderen.

Danach wählt `edit` mit der **Score-Formel** (`backend/planner/scoring.py`) aus CLAUDE.md:
`Score = Stimmung + Energie-Passung + Qualität − Wiederholung − Überhang − Dialog`, jeweils mit Gewicht (Überhang siehe Streuung unten). Die Ziel-Stimmung und die Gewichte jedes Stils stehen in `backend/styles/<stil>.yaml` (romance, hype, sad, funny, story). Mit Stil kommen nur die 30 % der Clips in Frage, die am besten zur Ziel-Stimmung passen, daraus nimmt der Planer pro Slot einen der 8 besten.

### So läuft die Figurenerkennung (Phase 5)

Auch das passiert in `index` (`backend/character_index.py`), erst pro Folge, dann einmal für die ganze Staffel:

1. **Gesichter finden** (`backend/analysis/video/faces.py`): Ein YOLOv8-Modell, das auf Anime-Gesichter trainiert ist ([deepghs/anime_face_detection](https://huggingface.co/deepghs/anime_face_detection)), schaut sich dieselben Standbilder an wie CLIP, nur größer (480 Pixel hoch). YOLO („You Only Look Once“) bewertet das ganze Bild in einem Durchgang und liefert Rahmen um alles, was wie ein Gesicht aussieht. Viele Rahmen liegen fast übereinander, die *Non-Maximum Suppression* behält davon nur den sichersten. Das Modell ist eine ONNX-Datei und läuft mit `onnxruntime`, ohne PyTorch. Gesichter kleiner als 7 % der Bildhöhe werden ignoriert, die sind zum Wiedererkennen zu klein.
2. **Gesicht als Embedding**: Um jedes Gesicht wird ein Quadrat ausgeschnitten, 1,8-mal so groß, damit Haare und Frisur mit drauf sind. CLIP macht daraus ein Embedding. Gesichter derselben Figur liegen dort nah beieinander (Haarfarbe, Frisur, Augen, Brille). Rahmen und Embeddings liegen pro Folge in `data/cache/faces/`.
3. **Vorbilder** (`backend/sources/character_images.py`): Die Bilder der Haupt- und Nebenfiguren von AniList werden einmal nach `data/cache/characters/` geladen. Eigene Screenshots in `data/characters/<Name>/` zählen zusätzlich (siehe unten).
4. **Zuordnen** (`backend/analysis/characters.py`): Die AniList-Bilder sind oft anders gezeichnet als die Folgen. Deshalb sucht das Tool zuerst nur die paar Gesichter, die einem Vorbild *eindeutig* ähnlicher sind als dem Rest der Staffel. Deren Durchschnitt ist das neue Vorbild, jetzt im Zeichenstil der Folgen, und mit ihm kommen in jeder Runde mehr sichere Treffer dazu. Am Ende gehört ein Gesicht zu der Figur, der es deutlich ähnlicher ist als der zweitähnlichsten. Ist es zwei Figuren etwa gleich ähnlich (oder keiner), bleibt es „unbekannt“. Die Sicherheit (0,5–1) steht pro Gesicht und Clip in der Datenbank.

Ändern sich nur die Vorbilder oder die Einstellungen unter `characters:`, wird nur neu zugeordnet, das dauert Sekunden. `edit --characters` nutzt das so (`backend/planner/assign.py`): Erst kommen Clips mit allen gewünschten Figuren dran, sind die aufgebraucht, Clips mit mindestens einer davon, erst dann Wiederholungen. Mit `--style` werden die 30 % ruhigsten (bzw. wildesten …) Clips unter diesen Clips gesucht, nicht in der ganzen Staffel. In der Score-Formel zählt `character` mit, wie sicher die Figuren im Bild sind. Außerdem kommt keine Figur öfter als zweimal hintereinander, außer denen aus `--characters`.

### So läuft die Streuung

Ohne Streuung nimmt der Planer pro Slot einfach einen der besten Clips. Die besten Action-Clips liegen aber oft dicht beieinander, z. B. 9 Clips aus einem 30-Sekunden-Kampf, und das Edit erzählt dann eine Szene nach. Deshalb gibt es zwei Regeln (`backend/planner/spread.py`, Einstellungen unter `planner:` in `default.yaml`):

1. **Höchstens 2 Clips pro Minute einer Folge** (`spread_max_clips: 2`, `spread_window_seconds: 60`): Ein Clip, mit dem eine Minute einer Folge schon 3 Clips im Edit hätte, kommt nicht in Frage, solange es an anderen Stellen noch passende Clips gibt. Gibt es keine mehr (knapper Pool, z. B. nur 71 Paar-Szenen für 81 Schnitte), steigt die Grenze auf 3, dann 4 …, statt Schnitte zu verlieren. Ein fester Mindestabstand (z. B. 30 s) würde um jeden Clip eine ganze Minute sperren, bei einem Kampf bliebe dann nur ein Clip, und Hype-Edits verlieren ihre besten Szenen.
2. **Überhang** (`weights.overuse: 0.3`): Kam eine Folge im bisherigen Edit schon deutlich öfter dran als der Durchschnitt, gibt es einen Abzug im Score. Beispiel: Nach 26 Clips aus 13 Folgen ist der Durchschnitt 2, eine Folge mit 3 Clips bekommt ein Drittel des Abzugs, ab 5 den vollen.

Wenn sich Wünsche widersprechen, gilt: gewünschte Figuren vor Stil vor Streuung vor Abwechslung (Folge/Figur nicht 3× hintereinander) vor Bewegungs-Peak auf dem Beat. Bei `--characters "Hori,Miyamura"` mit 81 Schnitten und 71 Paar-Szenen werden deshalb alle Paar-Szenen genommen, egal wo sie liegen; die Streuung wirkt erst, wenn es mehr passende Clips als Schnitte gibt. Am Ende zeigt `edit` die dichteste Stelle („Dichteste Stelle: 2 Clips aus Folge 1 zwischen 10:18.0 und 10:44.0“), in der `.plan.json` steht pro Clip `crowd` (so viele Clips kamen bei der Wahl aus derselben Minute der Folge).

### So laufen Stil-Profile, Effekte und Reframe (Phase 6)

1. **Tempo des Stils** (`backend/planner/song_slots.py`): Jeder Stil ist für einen BPM-Bereich gemacht (Romance 70–100, Hype 140–175, Sad 60–90, Funny 100–130, Story egal). Liegt der Song daneben, wird im halben oder doppelten Tempo gezählt: Ein 150-BPM-Song wird für Romance wie 75 BPM geschnitten, also mit doppelt so vielen Beats pro Clip. Die Schnittrate pro Abschnitt steht unter `cuts:` im Stil und überschreibt `cuts:` aus `default.yaml`.
2. **Tempo der Clips** (`backend/render/effects.py`): Slow-Mo (Romance 0,8×, Sad 0,75×) und Speed-Ramps (Hype: nach dem Schnitt 0,5×, dann 1,6×) stehen schon vor der Clip-Wahl fest, denn ein Slot in Zeitlupe braucht weniger vom Clip. Der Bewegungs-Peak landet trotzdem genau auf dem Beat, gerechnet wird in Clip-Zeit (`Timing` in `backend/planner/slots.py`).
3. **Reihenfolge:** `story` nimmt die Clips in der Reihenfolge der Staffel, Folge für Folge, und lässt dabei hinten genug Clips für die restlichen Schnitte übrig. Dafür entfällt dort „nicht 3× dieselbe Folge hintereinander“.
4. **Übergänge:** An jedem Schnitt gilt die erste passende Regel aus dem Stil: erster Schnitt im Drop (`drop`), neuer Abschnitt (`section`), Schnitt auf einem Taktanfang (`downbeat`), sonst `default`. Bei **Crossfade** und **Whip** sind kurz beide Clips zu sehen, die Mitte der Überblendung liegt genau auf dem Beat. **Dip to White** und **Fade to Black** blenden erst in die Farbe und dann heraus, **Flash** blitzt weiß auf und blendet ins neue Bild. Ein Übergang ist höchstens halb so lang wie der kürzere Clip, im Drop mit Halbbeat-Schnitten wird deshalb oft hart geschnitten.
5. **Effekte pro Clip:** **Zoom-Punch** (kurz 10–12 % ranzoomen, am Schnitt, auf jeder Eins oder jedem Beat), **Shake** (der Ausschnitt wackelt), **Push-In** (über den Clip langsam ans Gesicht heran), **Freeze-Frame** (Funny: der Clip läuft, auf dem Beat mit dem Bewegungs-Peak bleibt das Bild stehen und zoomt aufs Gesicht). `zones` begrenzt einen Effekt auf bestimmte Abschnitte, `min_seconds` auf längere Clips.
6. **Farblook** (`backend/render/looks.py`): Ein Look ist eine **3D-LUT**, eine Tabelle „aus dieser Farbe wird jene“ für ein Raster von 17×17×17 Farben, ffmpeg rechnet für jeden Pixel dazwischen. Die Looks `warm`, `punchy`, `cold`, `bright` und `film` stehen als Zahlen (Wärme, Sättigung, Kontrast …) unter `fx.looks` in `default.yaml`, das Tool schreibt daraus `.cube`-Dateien nach `data/cache/looks/`. Eigene `.cube`-Dateien (z. B. aus DaVinci Resolve) gehen auch. Dazu kommen **Soft Glow** (eine unscharfe Kopie des Bilds wird hell darübergelegt) und **Vignette** (dunklere Ecken).
7. **Smart Reframe** (`backend/render/reframe.py`): Aus einem 16:9-Bild passt nur knapp ein Drittel der Breite ins 9:16-Format. Wo dieses Drittel liegt, entscheiden die Gesichter aus Phase 5 (mit `--characters` die gewünschten Figuren, sonst alle großen Gesichter): Passen sie zusammen rein, kommt der Ausschnitt in ihre Mitte; bewegen sie sich zwischen den Standbildern, wandert er mit; stehen zwei zu weit auseinander, schwenkt er in Clips ab 1,2 s langsam vom einen zum anderen, in kürzeren bleibt er auf dem wichtigsten Gesicht. Ohne Gesichter folgt er dem **Bewegungsschwerpunkt**: Optical Flow wie in Phase 3, davon wird die Bewegung der Kamera abgezogen (der Median aller Bewegungen), übrig bleibt, was sich selbst bewegt. Sonst die Mitte.
8. **Rendern** (`backend/render/ffmpeg_graph.py`): Jeder Clip ist weiterhin ein eigenes, frame-genaues Stück. Neu darin: `setpts` (Tempo), `crop` mit wanderndem x (Reframe), `zoompan` (Zooms), bewegter `crop` (Shake), `lut3d` (Look), `xfade` (Überblendungen) und `fade` (Flash, Dips). Die Länge bleibt Frame für Frame gleich, jeder Schnitt liegt weiter auf dem Beat.

### So laufen Songvorschläge (Phase 7)

1. **Songs holen** (`backend/sources/music.py`, `backend/sources/jellyfin.py`): Im Ordner-Modus zählen alle MP3/FLAC/WAV/M4A/OGG/OPUS/AAC-Dateien, auch in Unterordnern. Titel, Interpret, Album und Genre kommen aus den Tags der Datei (ffprobe), fehlen sie, aus dem Dateinamen („LiSA - Gurenge.mp3“). Aus Jellyfin kommen alle Songs (Typ Audio) mit Tags und Länge über die API, die Datei wird erst heruntergeladen, wenn der Song analysiert wird (nach `data/cache/music/`). Songs unter 45 Sekunden (Jingles, Intros) und über 12 Minuten (Mixe) fallen raus.
2. **Struktur** wie in Phase 3 (`backend/songs.py`): Tempo, Beats, Abschnitte, Drops, Energiekurve. Was `song` oder `edit` schon analysiert haben, wird nicht neu gerechnet.
3. **Messwerte und Arousal/Valenz** (`backend/analysis/music/mood.py`): Psychologen beschreiben Stimmungen gern mit zwei Achsen. *Arousal* ist, wie aufgeregt etwas klingt (ruhig … energiegeladen), *Valenz*, wie angenehm (traurig … fröhlich). Arousal kommt aus Tempo, Anschlägen pro Sekunde, dem Anteil Schlagzeug (HPSS trennt das Spektrogramm in stehende Töne und kurze Schläge), der Helligkeit des Klangs und der Lautstärke, Valenz vor allem aus Dur oder Moll. Jede Stimmung hat einen Platz in diesem Feld (Action: aufgeregt, eher fröhlich; Sad: ruhig, düster …), je näher der Song daran liegt, desto stärker die Stimmung.
4. **Tonart** (`backend/analysis/music/key.py`): Pro Halbton wird gemessen, wie viel davon im Song vorkommt (Chroma). Diese 12 Werte werden mit typischen Profilen aller 24 Dur- und Moll-Tonarten verglichen (Krumhansl-Schmuckler), die ähnlichste gewinnt. Die Tonart steht in der Ausgabe („a-Moll“) und zählt über Dur/Moll für die Valenz.
5. **CLAP** (`backend/analysis/music/clap.py`): CLAP („Contrastive Language-Audio Pretraining“) ist CLIP für Ton. Ein Modell rechnet Audio und Sätze in denselben Zahlenraum um (ein *Embedding*), ein Song und eine passende Beschreibung landen nah beieinander. Das Tool hört sich 6 Stücke à 10 Sekunden aus jedem Song an (verteilt, ohne die ersten und letzten 10 %) und vergleicht sie mit den Sätzen aus `backend/prompts/music_prompts.yaml` („a tender love ballad with soft vocals“, „epic powerful battle music“ …). Das Modell ist [laion/larger_clap_music](https://huggingface.co/laion/larger_clap_music), es läuft über `transformers`, das mit dem Satz-Modell aus Phase 4 schon installiert ist. Die Embeddings liegen in `data/cache/clap_music/`, neue Sätze brauchen deshalb kein neues Anhören.
6. **Stimmung** (`backend/music_index.py`): Messwerte (Gewicht 0,4) und CLAP (1,0) werden gemischt, wie die Signale der Clips in Phase 4. Ohne CLAP zählen nur die Messwerte. Jeder Song merkt sich einen Fingerabdruck aus Datei, Einstellungen, CLAP-Modell und Sätzen, ein zweiter Lauf rechnet nichts neu. Kaputte Dateien landen in `data/cache/music/failed.json` und werden beim nächsten Lauf übersprungen.
7. **Vorschläge** (`backend/planner/suggest.py`): Für jeden Song wird der Ausschnitt gewählt, den `edit` nehmen würde (stärkster Drop bei 40 %, Tempo halb/doppelt wie in Phase 6). Dann gibt es fünf Teilnoten von 0 bis 1:
   - **Stimmung:** Passt die Stimmung des Songs zur Ziel-Stimmung des Stils (`mood:` in `backend/styles/<stil>.yaml`)?
   - **Staffel:** Klingt der Song wie die Szenen der Staffel, die für den Stil in Frage kommen (dieselben 30 % wie bei `edit`)? Verglichen wird nur die Form (welche Stimmungen über dem Durchschnitt liegen), weil Clips und Songs verschieden gemessen werden.
   - **Tempo:** Liegt der Song im BPM-Bereich des Stils? Halbes oder doppeltes Tempo gibt etwas Abzug, 40 % daneben zählt nichts mehr.
   - **Drop:** Liegt ein Drop im Ausschnitt? Hype will einen (`song: drop: 1.0` im Stil), Romance und Sad lieber nicht (−0,3 bzw. −0,5).
   - **Material:** Gibt es genug passende Clips für die Schnitte (2 pro Schnitt)? Wichtig mit `--characters`.

   Der gewichtete Mittelwert ist „passt zu X %“. Gute Teilnoten werden als Gründe genannt, schwache als Einwände. Höchstens 2 Songs pro Interpret, damit die Liste nicht nur aus einem Album besteht.

## Installation (Windows)

Voraussetzungen: Python 3.10 oder 3.11 und ffmpeg im PATH (`ffmpeg -version` muss funktionieren).

```powershell
git clone https://github.com/StrikerLUL/amv-forge.git
cd amv-forge
py -3 -m venv .venv
.venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

Die `torch`-Zeile installiert PyTorch mit CUDA für NVIDIA-Grafikkarten (ab Phase 4 für CLIP, Silero und das Satz-Modell). Ohne sie holt `pip install -r requirements.txt` die CPU-Version, das geht auch, ist aber langsamer. Mehr dazu unten bei Phase 4.

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

## Benutzung: Stimmung und Stile (Phase 4)

### Pakete installieren (einmal)

Phase 4 braucht PyTorch, CLIP (`open_clip_torch`), Silero VAD, `pysubs2` und `sentence-transformers`. PyTorch zuerst und **mit CUDA**, sonst rechnet alles auf der CPU (in der aktivierten venv):

```bat
pip uninstall -y torch torchvision
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

Die letzte Zeile muss `True` ausgeben (z. B. `2.x.x+cu128 True`). Steht da `+cpu` oder `False`, ist die CPU-Version drin. Meldet pip „No matching distribution“, auf https://pytorch.org/get-started/locally/ Windows, Pip, Python und die oberste CUDA-Version wählen und die angezeigte Zeile nehmen.

Beim ersten `index` lädt das Tool zwei Modelle aus dem Internet (einmalig, landen in `%USERPROFILE%\.cache\huggingface`): CLIP ViT-B-32 (ca. 600 MB) und das Satz-Modell für Untertitel (ca. 470 MB). Silero VAD steckt schon im pip-Paket. Fehlt ein Paket oder klappt ein Download nicht, läuft `index` ohne dieses Signal weiter und sagt das in der Ausgabe.

### Staffel neu indizieren

Nur die neuen Schritte werden gerechnet (Standbilder + CLIP, Ton, Untertitel), OP/ED, Szenen und Bewegung kommen aus der Datenbank:

```bat
python -m backend.cli index --source folder --path "P:\Anime\Horimiya\S1"
```

### Anschauen, was erkannt wurde

```bat
python -m backend.cli moods --season 1
python -m backend.cli moods --season 1 --style romance
```

`moods` zeigt, welche Signale da sind, wie viele Clips wegen Bildqualität rausfliegen, die Top 5 pro Stimmung (mit dem CLIP-Satz, der am besten passt, und dem Untertitel) und schreibt Kontaktbögen nach `data/renders/stimmung_s1_<stimmung>.jpg`: die 24 passendsten Clips als Vorschaubilder.

### Edit mit Stil

```bat
python -m backend.cli edit --season 1 --song "C:\Musik\song.mp3" --style romance --seed 7 --preview --out data\renders\romance.mp4
python -m backend.cli edit --season 1 --song "C:\Musik\song.mp3" --style hype --seed 7 --preview --out data\renders\hype.mp4
```

Am Ende steht, welche Stimmung im Edit steckt („Stärkste Stimmung pro Clip: romance 40, calm 25 …“). In der `.plan.json` stehen pro Clip Stimmungsvektor, Qualität, Sprachanteil und Score. Ohne `--style` wählt `edit` wie in Phase 3 nur nach Bewegung, sortiert aber schlechte Bilder aus.

### Nachjustieren ohne Code

| Datei | Was du änderst | Was danach neu gerechnet wird |
| --- | --- | --- |
| `backend/prompts/clip_prompts.yaml` | Sätze für CLIP (z. B. mehr Romance-Motive) | Nur der Vergleich, Sekunden |
| `backend/prompts/dialog_prompts.yaml` | Beispielsätze für Untertitel | Nur die Untertitel |
| `default.yaml` → `mood.weights` | Wie stark CLIP, Bewegung, Ton, Untertitel zählen | Nur die Mischung, Sekunden |
| `default.yaml` → `quality` | Ab wann ein Clip als schwarz/Text/unscharf gilt | Nur die Mischung |
| `backend/styles/*.yaml` | Ziel-Stimmung, Pool-Größe, Gewichte der Score-Formel, ab Phase 6 Tempo, Schnittrate, Übergänge, Effekte, Look | Nichts, wirkt sofort beim nächsten `edit` |
| `default.yaml` → `planner` | Streuung: `spread_max_clips`, `spread_window_seconds`, `weights.overuse` | Nichts, wirkt sofort beim nächsten `edit` |

Nach Änderungen an den Prompts, Gewichten oder Qualitäts-Schwellen einmal `index` laufen lassen.
Tipp OneDrive: Die Vorschaubilder sind etwa 15 KB pro Clip (bei einer Staffel einige Tausend Dateien). Wer das nicht in OneDrive haben will, setzt `keyframes.cache_dir` und `clip.cache_dir` in einer eigenen YAML auf einen Ordner außerhalb.

### Optional: allin1 (genauere Song-Analyse)

allin1 braucht PyTorch (wie in Phase 4 installieren), madmom und NATTEN. NATTEN muss unter Windows selbst kompiliert werden und scheitert dort oft. Ohne allin1 läuft alles mit dem librosa-Fallback, das Tool sagt dir in der Ausgabe, welcher Analysator benutzt wurde.

Versuch es so (in der aktivierten venv):

```powershell
pip install git+https://github.com/CPJKU/madmom
pip install natten
pip install allin1
python -c "import allin1; print('allin1 ok')"
```

Klappt `pip install natten` nicht, nicht stundenlang daran festbeißen: Entweder beim librosa-Fallback bleiben oder AMV-Forge in WSL2 (Ubuntu unter Windows) laufen lassen, dort gibt es fertige NATTEN-Pakete. Nach einer erfolgreichen Installation einen schon analysierten Song mit `song "<pfad>" --analyzer allin1` neu analysieren. allin1 nutzt die NVIDIA-GPU, wenn PyTorch CUDA findet, das steht in der Ausgabe.

## Benutzung: Figuren (Phase 5)

### Paket installieren (einmal)

```bat
pip install -r requirements.txt
```

Neu ist nur `onnxruntime` für den Gesichtsdetektor. Beim ersten `index` lädt er das Modell von Hugging Face (einmalig, wie CLIP). Der Detektor ist klein und rechnet auf der CPU, die Ausgabe sagt „Gesichtsdetektor rechnet auf der CPU“. Das reicht. Wer ihn auf die Grafikkarte legen will: `pip uninstall -y onnxruntime` und dann `pip install onnxruntime-directml` (Windows, läuft über DirectX 12 auf jeder Grafikkarte). Die Ausgabe sagt dann „rechnet auf der GPU (DmlExecutionProvider)“. CLIP für die Gesichter läuft wie in Phase 4 auf der GPU, wenn PyTorch CUDA findet.

### Staffel neu indizieren

```bat
python -m backend.cli index --source folder --path "P:\Anime\Horimiya\S1"
```

Neu gerechnet werden nur die Gesichter (pro Folge ein Durchlauf) und die Zuordnung. Pro Folge steht da z. B. „Folge 3: 912 Gesichter in 301 von 380 Clips“, am Ende „Figuren zugeordnet: … von … Gesichtern“, wie viele Clips jede Figur hat und wie oft die Hauptfiguren zusammen im Bild sind. Für die Bilder von AniList muss `index` ohne `--no-api` laufen.

### Anschauen, was erkannt wurde

```bat
python -m backend.cli characters --season 1
python -m backend.cli characters --season 1 --show "Hori,Miyamura"
```

`characters` zeigt pro Figur Clips, Gesichter, durchschnittliche Sicherheit und Zahl der Vorbilder und schreibt Kontaktbögen nach `data/renders/`:

- `figur_s1_<name>.jpg`: links das Vorbild (so wie CLIP es sieht), dann Gesichter dieser Figur von „ganz sicher“ bis „gerade noch“ (`p0.95` … `p0.62`). Sind auch die unsicheren noch die richtige Figur, passt alles.
- `figur_s1_unbekannt.jpg`: die größten Gesichter, die keiner Figur zugeordnet wurden. Ist hier oft Hori dabei, wurde sie verpasst.
- `figuren_s1_hori_miyamura.jpg`: Szenen, in denen beide erkannt wurden.

Ohne `--show` gibt es Bögen für alle Hauptfiguren.

### Edit mit Figuren

```bat
python -m backend.cli edit --season 1 --song "C:\Musik\song.mp3" --characters "Hori,Miyamura" --style romance --seed 7 --preview --out data\renders\paar.mp4
```

Ein Teil des Namens reicht („Hori“ findet „Kyouko Hori“, bei gleichem Nachnamen gewinnt die Hauptfigur). Die Ausgabe zeigt, welche Figur gemeint ist, wie viele Clips beide zusammen haben und am Ende „Figuren im Edit (… Clips): beide 21, nur Kyouko Hori 2, nur Izumi Miyamura 1, ohne 0“. In der `.plan.json` stehen pro Clip die erkannten Figuren mit Sicherheit.

### Wenn eine Figur schlecht erkannt wird

Häufigster Grund: Das AniList-Bild sieht anders aus als die Figur im Anime. Dann eigene Vorbilder dazulegen, 3–5 Screenshots, auf denen das Gesicht gut zu sehen ist (PNG oder JPG, ein Ordner pro Figur, der Name wird wie bei `--characters` gesucht):

```text
data\characters\Hori\1.png
data\characters\Hori\2.png
data\characters\Miyamura\1.png
```

Danach `index` wie oben: Es wird nur neu zugeordnet, das dauert Sekunden. Die Einstellungen stehen unter `characters:` in `default.yaml`:

| Einstellung | Wirkung |
| --- | --- |
| `min_probability` (0.6) | höher = weniger, aber sicherere Treffer; niedriger = mehr Treffer, mehr Verwechslungen |
| `roles` | welche Figuren von AniList gesucht werden (`MAIN`, `SUPPORTING`, `BACKGROUND`) |
| `scale` (50) | wie schnell ein kleiner Abstand zwischen zwei Figuren als sicher gilt |
| `faces.min_size` (0.07) | kleinste Gesichtsgröße (Anteil der Bildhöhe), kleiner = mehr Gesichter aus der Ferne |
| `planner.max_same_character_in_row` (2) | so oft hintereinander darf dieselbe Figur kommen (ohne `--characters`) |

Bekannte Grenze: Eine Nebenfigur, die in der Staffel kaum vorkommt, kann Gesichtern von Figuren ohne AniList-Eintrag zugeordnet werden. Das sieht man in `characters` (viele Clips für eine kleine Rolle). Für die Hauptfiguren und `--characters` spielt das kaum eine Rolle.

## Benutzung: Streuung

Läuft bei `edit` und `quick` automatisch. Zum Vergleich mit dem alten Verhalten eine eigene YAML anlegen, z. B. `data\ohne_streuung.yaml`:

```yaml
planner:
  spread_max_clips: 0
  weights:
    overuse: 0
```

```bat
python -m backend.cli edit --season 1 --song "C:\Musik\song.mp3" --style hype --seed 7 --preview --out data\renders\hype_alt.mp4 --config data\ohne_streuung.yaml
python -m backend.cli edit --season 1 --song "C:\Musik\song.mp3" --style hype --seed 7 --preview --out data\renders\hype.mp4
```

Mit demselben Seed ist das erste Edit genau das von vorher. Die Zeilen „Folgen im Edit“ und „Dichteste Stelle“ zeigen den Unterschied. Wer noch mehr Streuung will, setzt `spread_max_clips: 1` (kostet bei Hype etwas Action), wer weniger will, `3`.

## Benutzung: Stile, Effekte und 9:16 (Phase 6)

Nichts zu installieren und nichts neu zu indizieren, `edit --style` macht jetzt einfach mehr:

```bat
python -m backend.cli edit --season 1 --song "C:\Musik\song.mp3" --style romance --seed 7 --preview --out data\renders\romance.mp4
```

| Stil | BPM | Schnitte | Übergänge | Effekte |
| --- | --- | --- | --- | --- |
| `romance` | 70–100 | alle 2–4 Beats, im Refrain 4 | Crossfade, Dip to White bei neuem Abschnitt und im Drop | warm, Soft Glow, Slow-Mo 0,8×, Push-In |
| `hype` | 140–175 | alle 2 Beats, Refrain 1, Build-up 4 → 1, Drop ½ | harte Cuts, Flash auf der Eins und im Drop, Whip bei neuem Abschnitt | punchy, Zoom-Punch auf der Eins, Shake im Drop, Speed-Ramps |
| `sad` | 60–90 | alle 4–8 Beats | lange Crossfades (1,5 Beats), Fade to Black | cold (entsättigt), Vignette, Slow-Mo 0,75×, Push-In |
| `funny` | 100–130 | alle 2–4 Beats | harte Cuts, Flash im Drop | bright, Freeze-Frame mit Zoom aufs Gesicht, Zoom-Punch |
| `story` | egal | alle 2–4 Beats | Cuts, Crossfade bei neuem Abschnitt, Flash im Drop | film, Clips in der Reihenfolge der Staffel |

Neue Zeilen in der Ausgabe:

- „Stil romance ist für 70-100 BPM gemacht, der Song hat 150 BPM: Schnittrate im halben Tempo gezählt (wie 75 BPM)“
- „Reframe 9:16 (Clips): Gesichter 40, Bewegung 20, Mitte 15, Schwenk 6“ und „Gesichter ganz im 9:16-Bild: 85 von 100 (85 %), aus der Mitte geschnitten wären es 40 (40 %)“
- „Übergänge (romance): crossfade 20, dip_white 3“ und „Effekte: tempo 18, push_in 9, Look warm, Glow 0.3“
- pro Clip in Klammern, was er bekommt, z. B. „(crossfade, tempo 0.8x, push_in)“

Neben dem Video liegt `<name>.reframe.jpg`: pro Clip das ganze 16:9-Bild, der 9:16-Ausschnitt hell, der Rest abgedunkelt, Gesichter grün (ganz im Bild), rot (abgeschnitten) oder grau (zählt nicht, Hintergrund). In der `.plan.json` stehen pro Clip `transition`, `effects`, `timing` (Tempo) und `framing` (Art, Position, Gesichter im Bild).

Optionen zum Vergleichen:

| Option | Wirkung |
| --- | --- |
| `--center` | 9:16 aus der Mitte wie bis Phase 5 (gilt auch für `quick`) |
| `--no-effects` | Clips und Schnittrate wie im Stil, aber ohne Übergänge, Effekte und Look |
| `--characters "Hori,Miyamura"` | der Ausschnitt geht auf diese Figuren, nicht aufs größte Gesicht |

### Nachjustieren ohne Code

Alles steht in `backend/styles/<stil>.yaml` (kommentiert) und unter `reframe:` und `fx:` in `default.yaml`. Am einfachsten eine Stil-Datei kopieren, z. B. `backend\styles\romance2.yaml`, die taucht dann bei `--style` als `romance2` auf.

| Wo | Was |
| --- | --- |
| Stil → `bpm` | für welches Tempo die Schnittrate gedacht ist (weglassen = Song wird nie halb/doppelt gezählt) |
| Stil → `cuts` | Beats pro Clip je Abschnitt (`beats_per_cut`), im Drop (`drop`), Build-up (`buildup_bars`, `buildup_from`, `buildup_to`) |
| Stil → `transitions` | `default`, `downbeat`, `section`, `drop` (cut, flash, crossfade, dip_white, fade_black, whip), `beats` = Länge |
| Stil → `effects` | `look`, `glow`, `vignette`, `slowmo`, `speed_ramp`, `zoom_punch`, `shake`, `push_in`, `freeze` (jeweils mit `zones` und `min_seconds`) |
| `fx.looks` | die Farblooks als Zahlen: `warmth`, `tint`, `saturation`, `contrast`, `brightness`, `lift` |
| `fx` | Länge von Flash und Whip, wie schnell der Punch zurückgeht, wie schnell der Shake wackelt |
| `reframe.too_wide` | Paar zu weit auseinander: `pan` (Schwenk), `main` (wichtigstes Gesicht), `fit` (ganzes Bild, oben und unten unscharf) |
| `reframe.margin`, `min_face_share` | Abstand der Gesichter zum Rand, ab welcher Größe ein Gesicht zählt |

Ein Tippfehler in einer Stil-Datei (z. B. `dorp:` statt `drop:`) bricht mit einer Meldung ab, die sagt, was erlaubt ist.

## Benutzung: Songvorschläge (Phase 7)

Nichts neu zu installieren. Beim ersten Lauf lädt das Tool das CLAP-Modell von Hugging Face (etwa 0,8 GB, einmalig, landet in `%USERPROFILE%\.cache\huggingface`). Klappt das nicht (kein Internet, `transformers` fehlt, kaputtes torchaudio), läuft alles ohne CLAP weiter, nur mit den Messwerten. Die Stimmung ist dann nur grob geschätzt: `music` und `suggest` sagen das mit einer Zeile „Achtung: … ohne CLAP“ (auch in der Vorschlagsdatei). Jeder neue `music`-Lauf probiert CLAP einmal; lädt es, rechnet er die Stimmung dieser Songs nach, sonst bleibt alles, wie es ist.

### Musik analysieren (einmal, dann nur neue Songs)

Aus einem Ordner (Unterordner zählen mit):

```bat
python -m backend.cli music --source folder --path "C:\Users\cilli\Music"
```

Oder aus Jellyfin (URL und API-Key aus der `.env` wie in Phase 2; der Jellyfin-Benutzer braucht die Download-Berechtigung):

```bat
python -m backend.cli music --source jellyfin --limit 40
```

| Option | Wirkung |
| --- | --- |
| `--limit 40` | höchstens 40 neue Songs pro Lauf, der Rest beim nächsten Mal (große Bibliotheken) |
| `--library Musik` | nur diese Jellyfin-Bibliothek (Standard: alle Songs in Jellyfin) |
| `--search LiSA` | nur Songs, deren Interpret, Titel, Album oder Dateiname das enthält |
| `--genre Pop` | nur Songs mit diesem Genre (ein Teil reicht) |
| `--list` | nichts analysieren, nur alle Songs mit Tempo, Tonart und Stimmung zeigen |
| `--force` | alles neu analysieren |

Ein einzelner Song geht wie bisher mit `song`, der zeigt jetzt auch Tonart und Stimmung (Zahlen als Beispiel):

```bat
python -m backend.cli song song.mp3
```

```
Tonart: a-Moll | Stimmung: romance 0.81, calm 0.62, sad 0.55, funny 0.31, action 0.12
Messwerte: 2.1 Anschläge/s, Schlagzeug 18 %, Helligkeit 1450 Hz, Lautstärke -11.2 dBFS, Dur 0.31 -> Arousal 0.28, Valenz 0.38 (ruhig, eher traurig/düster)
CLAP: am ähnlichsten "a tender love ballad with soft vocals" (romance 0.92, calm 0.60, sad 0.48)
```

### Vorschläge holen

```bat
python -m backend.cli suggest --season 1 --style romance
```

```
Horimiya, Stil romance: 1520 von 5066 Clips kommen in Frage
Stimmung dieser Clips: romance 0.71, calm 0.66, sad 0.41, funny 0.38, action 0.22
1. Duo - Slow Love (04:52.3) | 82 BPM, Es-Dur | romance 0.84, calm 0.63 | passt zu 81 %
   Warum: klingt romantisch und ruhig wie die romance-Szenen von Horimiya (CLAP: "a tender love ballad with soft vocals"); 82 BPM passt zu romance (70-100)
   Ausschnitt 01:12.4 bis 01:42.4, 14 Schnitte
   python -m backend.cli edit --season 1 --style romance --song "C:\Users\cilli\Music\Duo - Slow Love.mp3"
...
```

(Song und Zahlen sind ein Beispiel.) Die Liste steht zusätzlich in `data\renders\vorschlaege_s1_romance.txt`, die letzte Zeile jedes Vorschlags ist der fertige `edit`-Befehl. Dieselbe Datei unter zwei Namen (z. B. `song.mp3` und derselbe Song im Musikordner) zählt nur einmal. Bei YouTube-Downloads zeigt das Tool „R2 - Blah Blah Blah“ statt „R2 - R2 - Blah Blah Blah (Official Visualiser)“; steht der YouTube-Kanal als Interpret in der Datei (z. B. NoCopyrightSounds), zählt für „höchstens 2 pro Interpret“ der Interpret aus dem Titel.

| Option | Wirkung |
| --- | --- |
| `--top 10` | mehr Vorschläge (Standard 5) |
| `--length 45` | für ein 45-s-Edit (Ausschnitt, Schnitte und Material werden dafür berechnet; der `edit`-Befehl bekommt `--length 45`) |
| `--characters "Hori,Miyamura"` | zählt nur die Szenen mit beiden; wenig Material wird als Einwand genannt |

### Wenn CLAP nicht lädt

Steht im Log `laion/larger_clap_music konnte nicht geladen werden (OSError: Could not load this library: ...\torchaudio\lib\libtorchaudio.pyd)`, passt das installierte torchaudio nicht zu torch (meist nach einem torch-Update auf die CUDA-Version). Neuere `transformers` laden torchaudio automatisch mit, sobald es installiert ist, und scheitern dann. AMV-Forge braucht torchaudio nicht (nur das optionale allin1), also weg damit:

```bat
python -m pip uninstall -y torchaudio
```

Danach `music` noch einmal mit demselben Ordner starten. Er rechnet nur die Stimmung der Songs nach, die ohne CLAP eingeordnet waren (Tempo und Abschnitte bleiben), und am Ende fehlt die Zeile „Achtung: … ohne CLAP“.

### Nachjustieren ohne Code

| Wo | Was |
| --- | --- |
| `backend/prompts/music_prompts.yaml` | die Sätze für CLAP pro Stimmung (Englisch); danach reicht ein neuer `music`-Lauf, der nur neu vergleicht |
| `music_mood.weights` | wie stark Messwerte und CLAP zählen (`clap: 0` = ohne CLAP) |
| `music_mood.arousal`, `valence`, `ranges`, `prototypes` | woraus Arousal und Valenz kommen und wo jede Stimmung im Feld liegt |
| `music_library` | Dateiendungen, Mindest- und Höchstlänge, Download-Ordner für Jellyfin |
| `suggest.weights` | wie wichtig Stimmung, Staffel, Tempo, Drop und Material für den Vorschlag sind |
| `suggest.max_per_artist`, `top` | höchstens so viele Songs pro Interpret, so viele Vorschläge |
| Stil → `song: drop:` | ob der Stil einen Drop will (1 = unbedingt, −1 = auf keinen Fall, 0 = egal) |

Spotify wird nicht benutzt: Dessen Audio-Analyse ist für neue Apps seit November 2024 gesperrt, deshalb läuft alles lokal auf deinen Dateien.

## Tests

```powershell
python -m pytest
```

## Rechtliches

Nur Folgen verwenden, die du legal besitzt. Anime-Ausschnitte und Songs sind urheberrechtlich geschützt: Für TikTok am besten mit `--no-music` exportieren und den Song über die TikTok-Soundbibliothek drunterlegen.
