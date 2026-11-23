# ChessVision

ChessVision is a free, open-source Linux chess-training and analysis project. Phase 1 provides reliable local Stockfish analysis and a command-line demonstration. It uses no paid APIs or cloud service.

## Features

- Local Stockfish analysis through `python-chess`
- FEN and chess-position validation
- Multiple principal variations with SAN, UCI, evaluation, mate, and depth data
- White-perspective evaluation: positive favors White; negative favors Black
- Safe engine cleanup, including context-manager support

## Fedora installation

```bash
sudo dnf install python3 python3-pip python3-gobject \
  gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good \
  pipewire xdg-desktop-portal xdg-desktop-portal-gnome stockfish

python3 -m venv --system-site-packages .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
python main.py
```

Stockfish is found from `PATH`; no Ubuntu-specific location is assumed.

## GNOME Wayland capture preview

On GNOME Wayland, launch the capture preview with:

```bash
python -m chessvision.gui
```

Choose **Start Screen Capture**. GNOME displays its native screen-sharing
dialog; select the monitor or application window you want to share. ChessVision
receives only that user-approved PipeWire stream. Select **Stop Capture** to
close both the PipeWire pipeline and portal session.

Drag a rectangle over the preview to select a chessboard candidate. The
selection is available as an RGB NumPy array to later phases, but this phase
does not use an external overlay.

The GUI needs an active GNOME Wayland user session, a working
`xdg-desktop-portal-gnome` service, PipeWire, and the Fedora
`python3-gobject` package. The virtual environment uses `--system-site-packages`
so it can access Fedora's GObject introspection bindings. Portal permissions may
be denied or cancelled, and the portal may restrict which source types it offers.

Known limitations: the portal controls source selection, so ChessVision cannot
request arbitrary desktop coordinates or bypass the GNOME permission dialog.
Only the first user-approved stream is previewed in this phase. Recognition is
local and template-driven; it is not a universal chess-theme recognizer.

## Board recognition and FEN confirmation

Phase 3 adds local OpenCV grid detection and offline template matching. After
selecting a board-sized capture rectangle, choose its orientation, optionally
enter a local template directory, and select **Analyze Board Image**. A
centered manual 8×8 grid fallback is used when a border cannot be detected.

The normal workflow is **Start Screen Capture → select Chessboard → Analyze
Board Image → correct squares if needed → Confirm Position → Analyze Confirmed
Position**. ChessVision uses bundled, original generated fallback templates on
first use, so a template directory is optional. Saved local templates override
the fallback when available.

The included user-supplied green/classic recognition profile is installed into
`~/.local/share/chessvision/profiles/verified_green_classic` on first use. The
GUI selects it automatically only when both the two-square colour palette and
piece-silhouette similarity match. The recognition status reports those checks
and the selected profile. A different theme is not forced through that profile:
it falls back to uncertainty/manual correction instead.

For a first-time setup, show a complete standard starting position, select its
board region, set the correct orientation, and choose **Calibrate Piece
Recognition**. After confirmation, ChessVision saves labeled square templates
under `~/.local/share/chessvision/templates` (or a directory you choose) and
loads that directory automatically in future GUI sessions. Calibration covers
all 12 piece types plus empty squares, but does not cover every piece on both
light and dark square colors; the GUI reports those missing combinations.

Templates are never downloaded automatically. Add legally usable PNG files
under a local directory such as:

```text
my-templates/
  white_pawn/pawn.png
  white_knight/knight.png
  black_pawn/pawn.png
  ...
```

Supported directory names are `white_pawn`, `white_knight`, `white_bishop`,
`white_rook`, `white_queen`, `white_king`, and their `black_` equivalents.
Multiple PNGs per state are supported. Recognition is intentionally
confidence-aware and theme-dependent; uncertain squares must be corrected in
the editable 8×8 board before confirming a position. Click an editor square
to inspect its original captured image, choose a corrected state from its
drop-down, and use **Undo Square Edit** if needed. Invalid recognized positions
(missing/multiple kings, impossible pawns, or excessive pieces) are flagged for
review; ChessVision never changes pieces merely to make a position legal.

FEN metadata cannot be inferred from a screenshot. Enter side to move,
castling rights, en-passant target, halfmove clock, and fullmove number, then
choose **Confirm Position**. ChessVision validates the FEN but never sends an
unconfirmed, uncertain, or invalid position to Stockfish.

Confirmed positions are analyzed locally at the selected depth. The GUI shows
three principal variations and draws green, blue, and orange arrows for MultiPV
1–3 on its internal board; it never controls an external chess client.

### Measuring recognition on real board themes

ChessVision does not bundle third-party website screenshots, so real-world
accuracy is not claimed from its synthetic tests. To evaluate a legally usable,
labeled local board-crop dataset, create a JSON manifest with `image`, `fen`,
and `orientation` fields and run:

```bash
python tools/evaluate_recognition.py fixtures/manifest.json --templates ~/.local/share/chessvision/templates
```

The script reports square-level accuracy and the uncertain-prediction rate
without uploading images or telemetry.

## Verified runtime

Phase 1 was verified locally with the following versions:

- Python 3.13.11 (fresh virtual environment) and Python 3.15.2
- `python-chess` 1.999 (`chess` 1.11.2)
- Stockfish 19

Both Python versions completed a real standard-position analysis at depth 12
with MultiPV 3 and exited cleanly. ChessVision uses `python-chess`'s
`UciProtocol` inside an `asyncio.Runner`, avoiding the threaded `SimpleEngine`
wrapper that stalled in this environment.

## Usage

```bash
python main.py
python main.py --fen "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
python main.py --depth 20 --multipv 5
python main.py --stockfish /usr/bin/stockfish
```

Run `python main.py --help` for all arguments. The program prints the board and requested best lines. CLI evaluations are in pawns, from White's perspective; mate is reported separately.

## Testing

```bash
pytest
```

The test suite mocks Stockfish where practical, so most tests run without an engine installation.

## Position sources and browser import

ChessVision has four position sources: **Browser**, **Screenshot**, **FEN / PGN**, and
**Manual**. Screenshot recognition is unchanged and remains a fallback; it deliberately
requires manual metadata before it can become a FEN. FEN/PGN import preserves all state
from a complete FEN or the final PGN position. The editable board supports all twelve
pieces/empty squares and Undo.

Browser importing is local, opt-in, and intended only for permitted analysis, completed
games, coaching, and explicit bot/training contexts. In the GUI select Browser and press
**Start Browser Import**. Copy the shown `http://127.0.0.1:PORT` address and one-session
token into the extension popup, then press **Import current board** in the popup and
**Import Browser Position** in ChessVision. The receiver listens only on `127.0.0.1`,
uses a fresh random token for every application run, accepts only extension origins, and
limits/parses JSON before accepting it. It stores no credentials or cookies.

Install the unpacked extension from `browser_extension/`:

- Firefox: visit `about:debugging#/runtime/this-firefox`, select **Load Temporary Add-on**,
  then select `browser_extension/manifest.json`.
- Chromium: visit `chrome://extensions`, enable **Developer mode**, select **Load unpacked**,
  then select the `browser_extension` directory.

The extension uses layered, generic adapters: exposed FEN-like placement first, then
`data-*` / square-and-piece DOM information. It does not call private APIs, inspect
network traffic, collect cookies, or infer missing turn/castling/en-passant/clocks from
pieces. Browser layout compatibility has not been verified against a live Chess.com page;
inspect every import. Unsupported or ambiguous pages report an error rather than inventing
a position.

Select an analysis mode before engine use. **Live competitive game** and **Uncertain /
display only** always disable engine recommendations and arrows. Imported browser data is
initially treated as unconfirmed; confirmation requires complete, valid metadata. The
application cannot determine fair-play authorization from page text, so the user must
choose a permitted mode only where analysis is explicitly allowed.

Troubleshooting: if pairing fails, keep ChessVision running, press Start Browser Import
again after restart, and replace both popup values; session tokens intentionally expire on
restart. If metadata is missing, use FEN/PGN import or the board editor and metadata panel.

## Continuous screenshot board tracking

After a screenshot-recognized (or FEN/PGN) position has been reviewed and confirmed, choose
**Confirm Initial Position**, select the same board crop, and press **Start Monitoring**.
ChessVision keeps the already-authorized PipeWire stream open and samples the selected crop
every second (adjustable from 250 ms to 10 s). It skips nearly identical images and never
runs overlapping recognition jobs.

For each stable recognized placement, ChessVision compares it against every legal next move
of the authoritative `python-chess` board. A move is accepted only when exactly one legal
result matches and the same confident observation arrives twice. Castling, captures, en
passant, promotion, clocks, side-to-move, castling rights, and en-passant targets therefore
come from the authoritative board state—not reconstructed from pixels. Transient animations,
uncertain squares, mismatches, and capture interruptions leave the tracked board unchanged
and show a warning. Use **Manual Resync** only after explicitly correcting and confirming a
position; **Undo Last Move** pops only a previously confirmed tracked move.

An unchanged recognized board is a valid monitoring heartbeat even when template scores for
empty/background squares are low. Confidence and uncertainty checks apply to a *changed*
placement before ChessVision considers legal successors. During monitoring, **Save Current
Recognition / Tracking Report** exports the current crop and local report. The on-screen
diagnostics list frame dimensions, one-time crop coordinates, locked orientation, selected
recognition profile, uncertain-square count, recognized versus authoritative placement,
square differences, confidence, stability, legal-candidate count, and the exact decision.

Monitoring displays board state and move history but does not automatically start Stockfish.
The existing fair-play mode restrictions remain in force throughout monitoring.

### Position, tracking, and engine status

These are deliberately separate states. A complete imported FEN/PGN is immediately a
**valid position** and can be analyzed in Offline, Completed Game, or Permitted Training
mode; it still needs **Confirm Initial Position** before screenshot monitoring starts.
Starting monitoring additionally requires an active portal capture and a selected board
region. A tracking mismatch or uncertain frame only affects **Tracking** and never removes
an already confirmed FEN or disables otherwise permitted offline engine analysis. Switching
the source selector alone also does not reset a confirmed position. Live Competitive and
Uncertain/Display-only modes always block engine recommendations.

## Roadmap

1. Stockfish engine and command-line analysis (current phase)
2. OpenCV-based screen and board detection (current phase)
3. Position recognition and FEN generation (current phase)
4. PySide6 transparent analysis overlay

## Disclaimer

ChessVision is for training and permitted analysis only. Do not use it for unauthorized assistance during competitive games or where analysis tools are prohibited.
