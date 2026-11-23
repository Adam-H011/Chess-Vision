from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import chess
from PySide6.QtWidgets import QApplication
from PySide6 import QtCore

from chessvision.board_detection import BoardGrid
from chessvision.gui import BoardCanvas, MainWindow
from chessvision.gui import PreviewWidget
from chessvision.piece_recognition import PiecePrediction
from chessvision.position import AnalysisMode, Position
from chessvision.capture import CaptureRegion
from chessvision.tracking import TrackingState


def fen_position() -> Position:
    return Position.from_fen(chess.STARTING_FEN)


def empty_predictions() -> dict[str, PiecePrediction]:
    return {f"{file_name}{rank}": PiecePrediction("empty", 1.0, False) for file_name in "abcdefgh" for rank in range(1, 9)}


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
