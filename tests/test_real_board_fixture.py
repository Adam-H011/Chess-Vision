"""Regression coverage for the user-supplied, legally local board crop.

The calibration image is never counted as evidence of generalization. The
second test moves a pawn onto an independently composed target square and uses
templates created only from the original starting-position crop.
"""

from __future__ import annotations

import chess
import cv2

from chessvision.board_detection import detect_board_grid
from chessvision.fen_builder import FenMetadata, build_fen
from chessvision.piece_recognition import (
    FEN_TO_PIECE,
    automatic_recognizer,
    calibrate_starting_position,
    install_verified_profile,
    recognize_board,
    select_recognizer,
)


FIXTURE = "tests/fixtures/real_board/board_crop.png"


def expected_states(fen: str) -> dict[str, str]:
    board = chess.Board(fen)
    return {
        chess.square_name(square): FEN_TO_PIECE[piece.symbol()]
        for square, piece in board.piece_map().items()
    }


def accuracy(predictions, expected: dict[str, str]) -> tuple[int, int]:
    occupancy = sum((item.state != "empty") == (expected.get(name, "empty") != "empty") for name, item in predictions.items())
    pieces = sum(item.state == expected.get(name, "empty") for name, item in predictions.items())
    return occupancy, pieces


def load_fixture():
    image = cv2.cvtColor(cv2.imread(FIXTURE), cv2.COLOR_BGR2RGB)
    return image, detect_board_grid(image)


def test_real_starting_position_occupancy_and_theme_templates(tmp_path) -> None:
    image, grid = load_fixture()
    expected = expected_states(chess.STARTING_FEN)
    raw = recognize_board(image, grid, automatic_recognizer(), "white")
    assert accuracy(raw, expected)[0] == 64  # Regression for confidently-empty pieces.
    calibrate_starting_position(image, grid, "white", tmp_path)
    themed = recognize_board(image, grid, automatic_recognizer(tmp_path), "white")
    assert accuracy(themed, expected) == (64, 64)
    assert build_fen(themed, FenMetadata("w", "KQkq", "-", 0, 1)) == chess.STARTING_FEN


def test_theme_templates_recognize_a_moved_pawn_composition(tmp_path) -> None:
    image, grid = load_fixture()
    calibrate_starting_position(image, grid, "white", tmp_path)
    altered = image.copy()
    # e2 and e4 are the same board colour. Move the visible pawn image there
    # and replace e2 with a real empty e4 crop from the original board.
    source = grid.extract_square(image, 6, 4)
    empty = grid.extract_square(image, 4, 4)
    e2 = grid.square_bounds(6, 4)
    e4 = grid.square_bounds(4, 4)
    altered[e2[1]:e2[3], e2[0]:e2[2]] = empty
    altered[e4[1]:e4[3], e4[0]:e4[2]] = source
    predictions = recognize_board(altered, grid, automatic_recognizer(tmp_path), "white")
    expected = expected_states("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    assert accuracy(predictions, expected) == (64, 64)


def test_theme_templates_map_a_black_bottom_view(tmp_path) -> None:
    image, grid = load_fixture()
    calibrate_starting_position(image, grid, "white", tmp_path)
    # Compose a black-bottom board from the same upright piece sprites. This
    # tests coordinate mapping separately from template labels.
    flipped = image.copy()
    for row in range(8):
        for column in range(8):
            source = grid.extract_square(image, row, column)
            x1, y1, x2, y2 = grid.square_bounds(7 - row, 7 - column)
            flipped[y1:y2, x1:x2] = cv2.resize(source, (x2 - x1, y2 - y1))
    predictions = recognize_board(flipped, grid, automatic_recognizer(tmp_path), "black")
    assert accuracy(predictions, expected_states(chess.STARTING_FEN)) == (64, 64)


def test_verified_profile_installs_and_is_selected_by_palette(tmp_path) -> None:
    image, grid = load_fixture()
    profile = install_verified_profile(tmp_path, FIXTURE)
    assert profile is not None and (profile.directory / "profile.json").exists()
    selection = select_recognizer(image, grid, profile_root=tmp_path)
    assert selection.profile_name == "verified_green_classic"
    assert selection.similarity == 0.0


def test_verified_profile_is_not_selected_for_unsupported_palette(tmp_path) -> None:
    image, _grid = load_fixture()
    install_verified_profile(tmp_path, FIXTURE)
    unsupported = image.copy()
    unsupported[:, :, 0] = 20
    unsupported[:, :, 1] = 20
    grid = detect_board_grid(unsupported)
    selection = select_recognizer(unsupported, grid, profile_root=tmp_path)
    assert selection.profile_name is None


def test_matching_palette_without_piece_shapes_does_not_select_profile(tmp_path) -> None:
    image, grid = load_fixture()
    install_verified_profile(tmp_path, FIXTURE)
    # Preserve the green/beige square palette, but remove all silhouettes.
    no_pieces = image.copy()
    light = grid.extract_square(image, 4, 4).copy()
    dark = grid.extract_square(image, 4, 3).copy()
    for row in range(8):
        for column in range(8):
            x1, y1, x2, y2 = grid.square_bounds(row, column)
            source = light if (row + column) % 2 == 0 else dark
            no_pieces[y1:y2, x1:x2] = cv2.resize(source, (x2 - x1, y2 - y1))
    selection = select_recognizer(no_pieces, grid, profile_root=tmp_path)
    assert selection.profile_name is None
