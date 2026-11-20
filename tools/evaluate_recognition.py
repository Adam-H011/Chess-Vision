"""Evaluate local ChessVision recognition against user-supplied labeled boards.

Manifest format:
[{"image": "board.png", "fen": "...", "orientation": "white"}]
Images must contain the board crop (small margins are allowed). No screenshots
are bundled, collected, or uploaded by this script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import chess
import cv2

from chessvision.board_detection import BoardGrid, detect_board_grid
from chessvision.piece_recognition import FEN_TO_PIECE, automatic_recognizer, recognize_board


def expected_states(fen: str) -> dict[str, str]:
    board = chess.Board(fen)
    return {
        chess.square_name(square): FEN_TO_PIECE[piece.symbol()]
        for square, piece in board.piece_map().items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate ChessVision against labeled local board crops.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--templates", type=Path, help="Optional local template directory")
    args = parser.parse_args()
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    correct = total = uncertain = 0
    for record in records:
        image = cv2.cvtColor(cv2.imread(str(args.manifest.parent / record["image"])), cv2.COLOR_BGR2RGB)
        try:
            grid = detect_board_grid(image)
        except ValueError:
            grid = BoardGrid.manual(image)
        predictions = recognize_board(image, grid, automatic_recognizer(args.templates), record.get("orientation", "white"))
        expected = expected_states(record["fen"])
        for name in (f"{file}{rank}" for file in "abcdefgh" for rank in range(1, 9)):
            predicted = predictions[name]
            correct += predicted.state == expected.get(name, "empty")
            uncertain += predicted.uncertain
            total += 1
    print(f"square accuracy: {correct}/{total} ({correct / total:.1%})")
    print(f"uncertain predictions: {uncertain}/{total} ({uncertain / total:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
