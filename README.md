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
sudo dnf install python3 python3-pip stockfish

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
python main.py
```

Stockfish is found from `PATH`; no Ubuntu-specific location is assumed.

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

## Roadmap

1. Stockfish engine and command-line analysis (current phase)
2. OpenCV-based screen and board detection
3. Position recognition and FEN generation
4. PySide6 transparent analysis overlay

## Disclaimer

ChessVision is for training and permitted analysis only. Do not use it for unauthorized assistance during competitive games or where analysis tools are prohibited.
