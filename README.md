# ChessVision – Stockfish-Brett-Assistent für Chrome

Markiere auf **beliebigen Websites** mit der Maus das Schachbrett. Die Extension liest die
Stellung aus dem Screenshot, baut daraus eine FEN und zeigt Stockfishs besten Zug als Pfeil
direkt auf dem markierten Brett an. Alles läuft lokal – keine API, kein Server, kein Login.

## Installieren

1. `chrome://extensions` öffnen
2. **Entwicklermodus** oben rechts aktivieren
3. **Entpackte Erweiterung laden** klicken und dieses Verzeichnis auswählen
4. Das ChessVision-Symbol an die Symbolleiste anheften (optional)

## Bedienung

1. Auf der Schachseite auf das ChessVision-Symbol klicken → **Brett jetzt markieren**.
   Das Popup schließt sich sofort und liegt danach nicht mehr über dem Brett.
   Alternativ geht im Overlay jederzeit der Button **⬚** (Brett neu markieren).
2. Mit gedrückter linker Maustaste ein Rechteck **genau um die 64 Felder** ziehen (nicht um Rand, Koordinaten oder Uhr)
3. Die Extension erkennt die Figuren, baut die FEN und zeichnet den besten Zug als grünen Pfeil
4. Danach läuft ChessVision im **Live-Modus** weiter: Sobald du auf der Website einen Zug machst,
   wird die Stellung automatisch neu erkannt und der nächste Zug vorgeschlagen – ohne erneutes Markieren.
5. Die Zugfarbe kommt immer aus **„Ich spiele als“** im Popup – sie wird nach deinem Zug **nicht**
   automatisch gewechselt. Im Overlay zeigt `Weiß`/`Schwarz` die analysierte Farbe; mit **W↔B**
   kannst du die Perspektive jederzeit manuell umschalten.
6. Bei Fehlern: im Overlay auf **✎** klicken. Die Felder sind nach Farbe markiert
   (weißer Ring = weiße Figur, schwarzer Ring = schwarze Figur). Im Auswähler gibt es getrennte
   Reihen für **Weiß** und **Schwarz**, einen Button **◐ Farbe wechseln** und **leer**.
   Du kannst beliebig viele Felder ändern; erst **✓ Übernehmen** gibt alle Änderungen an Stockfish.
   **↺** verwirft alle Änderungen, **✕** bricht ab.

Weitere Overlay-Buttons: **●/○ LIVE** mit **⏸/▶** Live-Analyse ein-/ausschalten, **↻** neu analysieren,
**⇄** Seite/Brett drehen, **✕** Overlay und Live-Analyse beenden.
Im Popup lassen sich die eigene Farbe und die Denkzeit (0,3–4 s) einstellen.

Hinweis: Website-eigene Dialoge (z. B. Werbe- oder Belohnungs-Pop-ups) können optisch stören.
Die Markierungs-Ebene liegt darüber, das Ziehen des Rechtecks funktioniert trotzdem.

## Funktionsweise

- `content/content.js` – Auswahlrechteck und Overlay (Shadow DOM, funktioniert auf allen Seiten)
- `background/service-worker.js` – koordiniert Screenshot, Offscreen-Dokument und Tabs
- `engine/host.html` + `engine/host.js` – Bilderkennung, FEN-Bau und Stockfish-Steuerung
- `engine/vision.js` – 8×8-Raster, Farberkennung und Figurenklassifikation per Kanten-/Silhouetten-Matching
- `sprites/` – mitgelieferte Chess.com-Neo-Vorlagen (aus dem früheren Python-Projekt)
- Zusätzlich werden Unicode-Schachglyphen in mehreren Fonts als Vorlagen erzeugt
- Der Live-Modus vergleicht etwa jede Sekunde einen frischen Screenshot mit dem letzten 8×8-Raster;
  erst wenn eine Änderung zweimal bestätigt wurde, wird mit der gewählten Zugfarbe neu analysiert
- Korrekturen werden als Vorlagen gelernt, damit dieselbe Seite beim nächsten Mal besser erkannt wird
- `engine/stockfish-19-lite-single.js/.wasm` – Stockfish 19 (Lite, single-threaded), ~1,8 MB
- `lib/chess.js` – FEN-Validierung, Legality-Check und SAN-Umwandlung

## Grenzen

- Die Erkennung ist Template-basiert. Auf ungewöhnlichen Figurensets können Fehler auftreten;
  die manuelle Korrektur (✎) ist deshalb bewusst eingebaut.
- Die Seite, auf der gespielt wird, muss sichtbar sein (`captureVisibleTab`). `chrome://`-Seiten
  können von Extensions nicht erfasst werden.
- Zugriff auf die Seite nur zur Anzeige – die Extension bewegt keine Figuren und sendet keine Züge.

## Lizenzen

- Stockfish: GPLv3 (siehe `engine/Stockfish-LICENSE.txt`)
- chess.js: MIT (siehe `lib/chess-LICENSE`)
- Figuren-Sprites: Chess.com Neo-Theme (aus dem ursprünglichen Projekt)

Nur für legales Training und Analyse verwenden.
