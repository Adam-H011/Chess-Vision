from __future__ import annotations

import json

import numpy as np

from chessvision.board_detection import BoardGrid
from chessvision.piece_recognition import PiecePrediction
from chessvision.recognition_diagnostics import export_debug_report, grid_overlay


def test_debug_export_contains_board_only_and_predictions(tmp_path) -> None:
    image = np.zeros((160, 160, 3), dtype=np.uint8)
    grid = BoardGrid.manual(image)
    tracking = {"authoritative_fen_before_observation": "example", "tracking_state": "uncertain-position", "candidate_scores": ["e2e4: exact"]}
    exported = export_debug_report(tmp_path, image, grid, {"a1": PiecePrediction("white_rook", .9, False)}, "test", tracking, image)
    assert (exported / "board_crop.png").exists()
    assert (exported / "grid_overlay.png").exists()
    assert (exported / "previous_board_crop.png").exists()
    report = json.loads((exported / "recognition.json").read_text())
    assert report["backend"] == "test"
    assert report["predictions"]["a1"]["state"] == "white_rook"
    assert report["tracking"] == tracking
    assert grid_overlay(image, grid).shape == image.shape
