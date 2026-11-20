from __future__ import annotations

import cv2
import numpy as np
import pytest

from chessvision.board_detection import BoardGrid
from chessvision.piece_recognition import (
    BuiltinPieceRecognizer, PiecePrediction, TemplateError, TemplatePieceRecognizer, TwoStagePieceRecognizer, _generated_template, calibrate_starting_position, classify_occupancy, recognition_warning,
    recognize_board,
)


def piece_shape(kind: str) -> np.ndarray:
    image = np.full((64, 64, 3), 230, dtype=np.uint8)
    if kind == "white_rook":
        cv2.rectangle(image, (18, 18), (46, 48), (20, 20, 20), 4)
    else:
        cv2.circle(image, (32, 32), 16, (20, 20, 20), 4)
    return image


def test_supported_template_recognition_and_confidence() -> None:
    empty = np.full((64, 64, 3), 230, dtype=np.uint8)
    recognizer = TemplatePieceRecognizer({"white_rook": [piece_shape("white_rook")], "empty": [empty]}, threshold=0.60)
    prediction = recognizer.recognize_square(piece_shape("white_rook"))
    assert prediction.state == "white_rook"
    assert prediction.confidence >= 0.95 and not prediction.uncertain
    assert TwoStagePieceRecognizer(recognizer).recognize_square(np.full((64, 64, 3), 230, dtype=np.uint8)).state == "empty"


def test_missing_templates_and_uncertain_prediction() -> None:
    with pytest.raises(TemplateError):
        TemplatePieceRecognizer({})
    recognizer = TemplatePieceRecognizer({"white_rook": [piece_shape("white_rook")]}, threshold=0.99)
    prediction = recognizer.recognize_square(piece_shape("black_knight"))
    assert prediction.uncertain


def test_template_loading_from_local_directory(tmp_path) -> None:
    folder = tmp_path / "white_rook"
    folder.mkdir()
    cv2.imwrite(str(folder / "rook.png"), cv2.cvtColor(piece_shape("white_rook"), cv2.COLOR_RGB2BGR))
    recognizer = TemplatePieceRecognizer.from_directory(tmp_path)
    assert recognizer.recognize_square(piece_shape("white_rook")).state == "white_rook"
    with pytest.raises(TemplateError):
        TemplatePieceRecognizer.from_directory(tmp_path / "absent")


def test_recognize_board_uses_grid_and_orientation() -> None:
    image = np.full((512, 512, 3), 230, dtype=np.uint8)
    image[:64, :64] = piece_shape("white_rook")
    grid = BoardGrid.manual(image)
    recognizer = TemplatePieceRecognizer({"white_rook": [piece_shape("white_rook")], "empty": [np.full((64, 64, 3), 230, dtype=np.uint8)]})
    predictions = recognize_board(image, grid, recognizer, "white")
    assert predictions["a8"].state == "white_rook"


@pytest.mark.parametrize("orientation", ["white", "black"])
def test_starting_position_calibration_persists_all_states(tmp_path, orientation: str) -> None:
    image = np.full((256, 256, 3), 180, dtype=np.uint8)
    image[:32, :32] = (255, 0, 0)  # Marker for the visual top-left square.
    grid = BoardGrid.manual(image)
    report = calibrate_starting_position(image, grid, orientation, tmp_path)
    assert sum(report.sample_counts.values()) == 64
    assert report.sample_counts["empty"] == 32
    assert report.sample_counts["white_pawn"] == 8
    assert (tmp_path / "white_king").is_dir()
    assert len(report.missing_square_color_combinations) > 0
    reloaded = TemplatePieceRecognizer.from_directory(tmp_path)
    assert "black_queen" in reloaded.templates and "empty" in reloaded.templates
    top_left_state = "black_rook" if orientation == "white" else "white_rook"
    saved = [cv2.imread(str(path)) for path in (tmp_path / top_left_state).glob("*.png")]
    assert any(np.any(sample[:, :, 2] == 255) for sample in saved)


def test_uncertain_piece_is_not_silently_replaced_with_empty() -> None:
    recognizer = TemplatePieceRecognizer({"white_rook": [piece_shape("white_rook")]}, threshold=0.99)
    prediction = recognizer.recognize_square(piece_shape("black_knight"))
    assert prediction.state == "white_rook"
    assert prediction.uncertain


def test_piece_confidence_is_a_real_similarity_not_an_inflated_score() -> None:
    recognizer = TemplatePieceRecognizer(
        {"white_rook": [piece_shape("white_rook")]}, threshold=.72
    )
    # A circular foreground overlaps the rook only partly. It remains a piece,
    # but its score must not be transformed into a misleadingly high value.
    prediction = recognizer.recognize_square(piece_shape("black_knight"))
    assert prediction.state == "white_rook"
    assert prediction.confidence < .72
    assert prediction.uncertain


def test_bundled_generated_recognizer_handles_its_original_templates() -> None:
    recognizer = TwoStagePieceRecognizer(BuiltinPieceRecognizer())
    assert recognizer.recognize_square(_generated_template("white_knight", 205)).state == "white_knight"
    assert recognizer.recognize_square(_generated_template("empty", 145)).state == "empty"


def test_occupancy_stage_keeps_foreground_from_becoming_confidently_empty() -> None:
    assert classify_occupancy(np.full((64, 64, 3), 230, dtype=np.uint8)).state == "empty"
    occupied = classify_occupancy(piece_shape("white_rook"))
    assert occupied.state == "occupied"
    warning = recognition_warning({"a1": PiecePrediction("empty", .95, False, "occupied", .22)})
    assert warning is not None
