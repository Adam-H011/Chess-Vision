"""Local, user-triggered diagnostics for board-recognition investigations."""

from __future__ import annotations

import json
import platform
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from .board_detection import BoardGrid
from .piece_recognition import PiecePrediction


def grid_overlay(image: np.ndarray, grid: BoardGrid) -> np.ndarray:
    """Draw the exact detected 8×8 grid on a board crop."""
    overlay = image[:, :, :3].copy()
    for index in range(9):
        x = round(grid.x + index * grid.square_width)
        y = round(grid.y + index * grid.square_height)
        cv2.line(overlay, (x, grid.y), (x, grid.y + (grid.board_height or grid.size)), (0, 220, 255), 1)
        cv2.line(overlay, (grid.x, y), (grid.x + (grid.board_width or grid.size), y), (0, 220, 255), 1)
    return overlay


def export_debug_report(
    directory: str | Path,
    board_image: np.ndarray,
    grid: BoardGrid,
    predictions: dict[str, PiecePrediction],
    backend: str,
) -> Path:
    """Export only the selected board crop and local recognition evidence."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(root / "board_crop.png"), cv2.cvtColor(board_image[:, :, :3], cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(root / "grid_overlay.png"), cv2.cvtColor(grid_overlay(board_image, grid), cv2.COLOR_RGB2BGR))
    squares = root / "squares"
    squares.mkdir(exist_ok=True)
    for row in range(8):
        for column in range(8):
            name = grid.square_name(row, column, "white")
            crop = grid.extract_square(board_image, row, column)
            cv2.imwrite(str(squares / f"{name}.png"), cv2.cvtColor(crop[:, :, :3], cv2.COLOR_RGB2BGR))
    report = {
        "backend": backend,
        "grid": asdict(grid),
        "predictions": {name: asdict(item) for name, item in predictions.items()},
        "software": {"python": platform.python_version(), "opencv": cv2.__version__},
        "note": "Contains only the user-selected board crop, never the full desktop frame.",
    }
    (root / "recognition.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return root
