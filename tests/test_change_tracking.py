from __future__ import annotations

import chess
import cv2
import numpy as np

from chessvision.board_detection import BoardGrid
from chessvision.change_tracking import ChangeFirstTracker, _affected_squares
from chessvision.tracking import BoardTracker, TrackingState


def frame(pieces: set[str], *, highlight: str | None = None) -> np.ndarray:
    image = np.zeros((512, 512, 3), dtype=np.uint8)
    for row in range(8):
        for col in range(8):
            image[row*64:(row+1)*64, col*64:(col+1)*64] = (235, 236, 208) if (row + col) % 2 else (115, 149, 82)
    if highlight:
        file_index, rank = "abcdefgh".index(highlight[0]), int(highlight[1])
        row = 8-rank
        image[row*64:(row+1)*64, file_index*64:(file_index+1)*64] = (80, 200, 220)
    for square in pieces:
        col, row = "abcdefgh".index(square[0]), 8-int(square[1])
        cv2.circle(image, (col*64+32, row*64+32), 18, (20, 20, 20), -1)
    return image


def test_change_first_detects_e4_without_classifying_every_piece():
    grid = BoardGrid.manual(frame({"e2"}))
    tracker = ChangeFirstTracker(change_threshold=.05, minimum_score=.10, separation=.01)
    tracker.set_baseline(frame({"e2"}), grid, "white")
    result = tracker.observe(frame({"e4"}), grid, chess.Board())
    assert result.candidates[0].move_uci == "e2e4"
    assert {"e2", "e4"}.issubset(result.changed_squares)


def test_highlight_only_does_not_create_move():
    base = frame({"e2"})
    grid = BoardGrid.manual(base)
    tracker = ChangeFirstTracker(change_threshold=.05, minimum_score=.10, separation=.01)
    tracker.set_baseline(base, grid, "white")
    result = tracker.observe(frame({"e2"}, highlight="e4"), grid, chess.Board())
    assert not result.candidates or not result.decision.startswith("Visual evidence favors")


def test_geometry_change_pauses_safely():
    base = frame({"e2"}); tracker = ChangeFirstTracker()
    tracker.set_baseline(base, BoardGrid.manual(base), "white")
    resized = cv2.resize(base, (700, 700))
    result = tracker.observe(resized, BoardGrid.manual(resized), chess.Board())
    assert result.geometry_warning == "board scale changed"


def board_frame(board: chess.Board) -> np.ndarray:
    return frame({chess.square_name(square) for square in board.piece_map()})


def test_alternating_white_and_black_moves_keep_visual_tracking_active():
    """Regression: duplicate settled frames confirm moves for both colours."""
    board = chess.Board()
    image = board_frame(board); grid = BoardGrid.manual(image)
    visual = ChangeFirstTracker(change_threshold=.05, minimum_score=.10, separation=.01)
    visual.set_baseline(image, grid, "white")
    legal = BoardTracker(); legal.confirm_initial(chess.STARTING_FEN)
    for uci, expected_turn in (("e2e4", chess.BLACK), ("e7e6", chess.WHITE), ("g1f3", chess.BLACK), ("g8f6", chess.WHITE)):
        next_board = board.copy(stack=False); next_board.push_uci(uci)
        next_image = board_frame(next_board)
        observation = visual.observe(next_image, grid, legal.board)
        assert observation.candidates[0].move_uci == uci
        # Two identical stable frames must be delivered, rather than dropping
        # the second one as an optimization.
        assert legal.observe_visual_move(uci, observation.candidates[0].score).state is TrackingState.CANDIDATE
        update = legal.observe_visual_move(uci, observation.candidates[0].score)
        assert update.state is TrackingState.MOVE_CONFIRMED
        assert legal.board.turn is expected_turn
        visual.accept_current(next_image, grid)
        board = next_board
    assert legal.history == ["e4", "e6", "Nf3", "Nf6"]


def test_visual_tracking_is_independent_of_configured_analysis_side():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    # Black's e5 is legal after White's e4 regardless of who receives engine advice.
    assert tracker.observe_visual_move("e2e4", .9).state is TrackingState.CANDIDATE
    tracker.observe_visual_move("e2e4", .9)
    assert tracker.board.turn is chess.BLACK
    assert tracker.observe_visual_move("e7e5", .9).state is TrackingState.CANDIDATE
    assert tracker.observe_visual_move("e7e5", .9).state is TrackingState.MOVE_CONFIRMED


def test_visual_tracking_ranks_all_legal_castles_above_partial_moves():
    cases = (
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1", ("e1", "f1", "g1", "h1")),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1c1", ("a1", "c1", "d1", "e1")),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "e8g8", ("e8", "f8", "g8", "h8")),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "e8c8", ("a8", "c8", "d8", "e8")),
    )
    for fen, uci, squares in cases:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves
        assert _affected_squares(board, move) == squares
        before = board_frame(board)
        after = board.copy(stack=False); after.push(move)
        visual = ChangeFirstTracker()
        grid = BoardGrid.manual(before)
        visual.set_baseline(before, grid, "white")
        observation = visual.observe(board_frame(after), grid, board)
        assert observation.changed_squares == squares
        assert observation.decision == f"Visual evidence favors {uci}."
        assert observation.candidates[0].move_uci == uci


def test_all_castles_keep_two_frame_confirmation_and_metadata():
    cases = (
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1", "O-O"),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1c1", "O-O-O"),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "e8g8", "O-O"),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "e8c8", "O-O-O"),
    )
    for fen, uci, san in cases:
        before = chess.Board(fen)
        after = before.copy(stack=False); after.push_uci(uci)
        image = board_frame(before); grid = BoardGrid.manual(image)
        visual = ChangeFirstTracker(); visual.set_baseline(image, grid, "white")
        legal = BoardTracker(); legal.confirm_initial(fen)
        observation = visual.observe(board_frame(after), grid, legal.board)
        assert legal.observe_visual_move(uci, observation.candidates[0].score).state is TrackingState.CANDIDATE
        update = legal.observe_visual_move(uci, observation.candidates[0].score)
        assert update.state is TrackingState.MOVE_CONFIRMED
        assert update.move_san == san
        assert legal.fen == after.fen(en_passant="fen")
