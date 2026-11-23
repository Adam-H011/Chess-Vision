from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import chess
import cv2
import chessvision.gui as gui_module
from PySide6.QtWidgets import QApplication
from PySide6 import QtCore

from chessvision.board_detection import BoardGrid
from chessvision.gui import BoardCanvas, MainWindow
from chessvision.gui import PreviewWidget
from chessvision.piece_recognition import PiecePrediction
from chessvision.position import AnalysisMode, Position
from chessvision.capture import CaptureRegion, FrameInfo
from chessvision.tracking import TrackingState


def fen_position() -> Position:
    return Position.from_fen(chess.STARTING_FEN)


def empty_predictions() -> dict[str, PiecePrediction]:
    return {f"{file_name}{rank}": PiecePrediction("empty", 1.0, False) for file_name in "abcdefgh" for rank in range(1, 9)}


def rendered_board(board: chess.Board) -> np.ndarray:
    """A compact board image with pawn-sized changes, like a board crop."""
    square_size = 80
    image = np.zeros((square_size * 8, square_size * 8, 3), dtype=np.uint8)
    for row in range(8):
        for col in range(8):
            image[row * square_size:(row + 1) * square_size, col * square_size:(col + 1) * square_size] = (235, 236, 208) if (row + col) % 2 else (115, 149, 82)
    for square, piece in board.piece_map().items():
        col, row = chess.square_file(square), 7 - chess.square_rank(square)
        color = (238, 238, 238) if piece.color else (35, 35, 35)
        cv2.circle(image, (col * square_size + 40, row * square_size + 40), 18, color, -1)
        cv2.circle(image, (col * square_size + 40, row * square_size + 40), 18, (15, 15, 15), 2)
    return image


def test_gui_renders_recognized_piece_in_editable_board() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    image = np.zeros((128, 128, 3), dtype=np.uint8)
    grid = BoardGrid.manual(image)
    window._recognition_complete(grid, {"a8": PiecePrediction("black_rook", 0.93, False)}, "test")
    assert window.editor.cellWidget(0, 0).currentText() == "black_rook"
    assert "0 uncertain" in window.statusBar().currentMessage()
    window.close()
    app.processEvents()


def test_move_arrow_coordinates_follow_orientation() -> None:
    canvas = BoardCanvas()
    canvas.resize(320, 320)
    canvas.orientation = "white"
    assert canvas._display_point("a1", 40).y() == 300
    canvas.orientation = "black"
    assert canvas._display_point("a1", 40).x() == 300


def test_letterboxed_preview_selection_maps_to_original_pixels() -> None:
    preview = PreviewWidget()
    preview.setMinimumSize(0, 0)
    preview.resize(400, 400)
    preview.set_frame(np.zeros((400, 800, 3), dtype=np.uint8))
    region = preview.source_region(QtCore.QRectF(100, 150, 200, 100))
    assert (region.x, region.y, region.width, region.height) == (200, 100, 400, 200)
    assert preview.source_region(QtCore.QRectF(0, 0, 20, 20)) is None


def test_compact_window_resizes_with_accessible_primary_controls() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.minimumWidth() == 750 and window.minimumHeight() == 500
    for width, height in ((750, 500), (1050, 700), (1500, 900)):
        window.resize(width, height)
        app.processEvents()
        assert window.start_button.isVisible() is False or window.start_button.isEnabled()
        assert window.analyze_button is not None
    window.close()


def test_editor_shows_source_square_and_supports_undo() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    image = np.full((128, 128, 3), 80, dtype=np.uint8)
    image[:16, :16] = (230, 230, 230)
    window._board_image = image
    window._grid = BoardGrid.manual(image)
    window._predictions = {"a8": PiecePrediction("black_rook", .61, True)}
    window._populate_editor()
    window._show_square_crop(0, 0)
    assert window.board_preview.pixmap() is not None
    window._manual_piece("a8", "white_queen")
    assert window._predictions["a8"].state == "white_queen"
    assert window.undo_button.isEnabled()
    window._undo_piece_edit()
    assert window._predictions["a8"].state == "black_rook"
    window.close()


def test_valid_fen_import_clears_stale_tracking_uncertainty_and_permits_offline_engine() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._tracker.confirm_initial(chess.STARTING_FEN)
    window._tracker.observe_placement(chess.STARTING_BOARD_FEN, uncertain=True)
    assert window._tracker.state is TrackingState.MONITORING
    window._apply_position(fen_position())
    assert window._position_valid and window._confirmed_fen == chess.STARTING_FEN
    assert window._tracker.state is TrackingState.WAITING_INITIAL
    assert "ready" in window.tracking_status.text().lower()
    assert window._selected_mode() is AnalysisMode.OFFLINE
    assert window.analyze_position_button.isEnabled()
    assert "permitted" in window.engine_status.text().lower()
    window.close(); app.processEvents()


def test_tracking_start_requires_confirmation_capture_and_region() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window._apply_position(fen_position())
    assert not window.monitor_start_button.isEnabled()
    window._confirm_tracking_initial()
    assert not window.monitor_start_button.isEnabled()
    window.capture._running = True
    window._monitor_region = CaptureRegion(0, 0, 80, 80)
    window._refresh_state_controls()
    assert window.monitor_start_button.isEnabled()
    window.close(); app.processEvents()


def test_tracking_mismatch_keeps_confirmed_fen_and_offline_engine_available() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window._apply_position(fen_position()); window._confirm_tracking_initial()
    window._monitoring_active = True
    image = np.zeros((80, 80, 3), dtype=np.uint8)
    window._monitor_frame_dimensions = (80, 80)
    window._monitor_region = CaptureRegion(0, 0, 80, 80)
    window._monitoring_recognized(BoardGrid.manual(image), empty_predictions(), None, "test")
    assert window._tracker.state is TrackingState.UNCERTAIN
    assert window._confirmed_fen == chess.STARTING_FEN
    assert window._position_valid and window.analyze_position_button.isEnabled()
    assert "Tracking:" in window.tracking_status.text()
    window.close(); app.processEvents()


def test_source_switch_and_engine_mode_do_not_reset_confirmed_position() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window._apply_position(fen_position())
    confirmed = window._confirmed_fen
    window.source_selector.setCurrentIndex(1)
    assert window._confirmed_fen == confirmed
    window.mode_selector.setCurrentIndex(3)  # live competitive
    assert not window.analyze_position_button.isEnabled()
    assert "blocked" in window.engine_status.text().lower()
    window.mode_selector.setCurrentIndex(0)  # offline
    assert window.analyze_position_button.isEnabled()
    assert "permitted" in window.engine_status.text().lower()
    window.close(); app.processEvents()


def test_white_recommendation_gate_resumes_after_a_confirmed_black_move() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window._apply_position(fen_position())
    assert window._may_recommend_now()  # White is the selected default side.
    board = chess.Board(); board.push_uci("e2e4")
    window._set_position_state(True, "tracked legal move confirmed", board.fen())
    assert not window._may_recommend_now()
    board.push_uci("e7e5")
    window._set_position_state(True, "tracked legal move confirmed", board.fen())
    assert window._may_recommend_now()
    window.close(); app.processEvents()


def test_fresh_small_pawn_move_reaches_change_tracking_and_a_fresh_settled_frame_confirms_it(monkeypatch) -> None:
    """Regression for the old global-MAD gate dropping a legal board change.

    A pawn-sized change over a complete board crop has a crop-wide MAD below
    one.  It must still reach the visual tracker, and a distinct PipeWire
    frame with the same settled pixels must remain eligible as the second
    stability observation.
    """
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    before = chess.Board()
    after = before.copy(stack=False); after.push_uci("e2e4")
    before_image, after_image = rendered_board(before), rendered_board(after)
    assert float(np.abs(before_image.astype(np.int16) - after_image.astype(np.int16)).mean()) < 1.0
    grid = BoardGrid.manual(before_image)
    window._tracker.confirm_initial(before.fen(en_passant="fen"))
    window._visual_tracker.set_baseline(before_image, grid, "white")
    window._monitor_last_frame = before_image
    window._monitor_last_sequence = 10

    class Signal:
        def connect(self, _callback):
            pass

    class Worker:
        submitted: list[np.ndarray] = []

        def __init__(self, image, *_args):
            self.image = image
            self.completed_monitoring = Signal()
            self.failed = Signal()

        def isRunning(self):
            return False

        def start(self):
            self.submitted.append(self.image)

    class Capture:
        def __init__(self):
            self.sequence = 11

        @property
        def is_running(self):
            return False

        def latest_frame_with_info(self):
            return after_image, FrameInfo(self.sequence, 1, 1, after_image.shape[1], after_image.shape[0])

        def stop(self):
            pass

    window.capture = Capture()
    window._monitor_region = CaptureRegion(0, 0, after_image.shape[1], after_image.shape[0])
    monkeypatch.setattr(gui_module, "MonitoringWorker", Worker)

    # Before the fix this fresh sequence was discarded, so no candidate formed.
    window._monitor_tick()
    assert len(Worker.submitted) == 1
    window._monitor_tick()  # Same sequence is not a second observation.
    assert len(Worker.submitted) == 1
    observed = window._visual_tracker.observe(after_image, grid, window._tracker.board)
    assert observed.candidates[0].move_uci == "e2e4"
    assert window._tracker.observe_visual_move("e2e4", observed.candidates[0].score).state is TrackingState.CANDIDATE

    # Sequence N+1 has the same settled pixels and is therefore valid
    # stability evidence, unlike a second poll of sequence N.
    window.capture.sequence = 12
    window._monitor_tick()
    assert len(Worker.submitted) == 2
    assert window._tracker.observe_visual_move("e2e4", observed.candidates[0].score).state is TrackingState.MOVE_CONFIRMED

    # The same sub-threshold admission applies to Black's compact e7→e6
    # pawn move; legal turn handling itself is covered by the alternating
    # change-tracking sequence test.
    black_after = after.copy(stack=False); black_after.push_uci("e7e6")
    black_image = rendered_board(black_after)
    assert float(np.abs(after_image.astype(np.int16) - black_image.astype(np.int16)).mean()) < 1.0
    window._visual_tracker.set_baseline(after_image, grid, "white")
    window._monitor_last_frame = after_image
    assert window._should_process_monitor_image(black_image)
    assert window._visual_tracker.observe(black_image, grid, window._tracker.board).candidates[0].move_uci == "e7e6"
    window.close(); app.processEvents()


def test_gui_processes_both_stable_castling_observations() -> None:
    app = QApplication.instance() or QApplication([])
    for turn, uci in (("w", "e1g1"), ("w", "e1c1"), ("b", "e8g8"), ("b", "e8c8")):
        window = MainWindow()
        before = chess.Board(f"r3k2r/8/8/8/8/8/8/R3K2R {turn} KQkq - 0 1")
        after = before.copy(stack=False); after.push_uci(uci)
        before_image, after_image = rendered_board(before), rendered_board(after)
        grid = BoardGrid.manual(before_image)
        window._tracker.confirm_initial(before.fen(en_passant="fen"))
        window._visual_tracker.set_baseline(before_image, grid, "white")
        window._monitoring_active = True
        window._monitor_worker = SimpleNamespace(image=after_image)
        visual = window._visual_tracker.observe(after_image, grid, window._tracker.board)
        window._monitoring_recognized(grid, {}, visual, "test")
        assert window._tracker.state is TrackingState.CANDIDATE
        window._monitor_worker = SimpleNamespace(image=after_image)
        window._monitoring_recognized(grid, {}, visual, "test")
        assert window._tracker.state is TrackingState.MOVE_CONFIRMED
        assert window._confirmed_fen == after.fen(en_passant="fen")
        window.close(); app.processEvents()


def test_manual_confirmation_cannot_desynchronize_legal_successor_turn_metadata() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    before = chess.Board(); before.push_uci("d2d4")
    window._tracker.confirm_initial(before.fen(en_passant="fen"))
    window._confirmed_fen = window._tracker.fen
    after = before.copy(stack=False); after.push_uci("d7d5")
    names = {"P":"white_pawn", "N":"white_knight", "B":"white_bishop", "R":"white_rook", "Q":"white_queen", "K":"white_king",
             "p":"black_pawn", "n":"black_knight", "b":"black_bishop", "r":"black_rook", "q":"black_queen", "k":"black_king"}
    window._predictions = {chess.square_name(square): PiecePrediction(names[piece.symbol()], 1, False) for square, piece in after.piece_map().items()}
    for key, value in (("turn", "b"), ("castling", "KQkq"), ("ep", "-"), ("halfmove", "0"), ("fullmove", "2")):
        window.metadata[key].setText(value)
    window._confirm_position()
    assert window._tracker.fen == before.fen(en_passant="fen")
    assert "metadata is stale" in window.statusBar().currentMessage()
    window.close(); app.processEvents()
    app.processEvents()
