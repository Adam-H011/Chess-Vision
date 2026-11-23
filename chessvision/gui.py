"""PySide6 preview UI for user-authorized GNOME Wayland capture."""

from __future__ import annotations

import sys
import logging
from pathlib import Path
import chess

try:
    import numpy as np
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover - requires optional GUI packages
    raise SystemExit("Install GUI dependencies with: python -m pip install -r requirements.txt") from exc

from .capture import CaptureError, CaptureRegion, PortalScreenCapture, crop_frame, InvalidRegionError
from .board_detection import BoardDetectionError, BoardGrid, detect_board_grid
from .fen_builder import FenBuildError, FenMetadata, build_fen, validate_fen
from .browser_bridge import BrowserBridge
from .position import AnalysisMode, Position, Source
from .tracking import BoardTracker, TrackingState
from .engine import ChessAnalyzer, EngineAnalysisError, StockfishNotFoundError
from .recognition_diagnostics import export_debug_report
from .piece_recognition import (
    PIECE_STATES, PiecePrediction, TemplateError, TemplatePieceRecognizer,
    PIECE_TO_FEN, automatic_recognizer, calibrate_starting_position,
    default_template_directory, recognition_warning, recognize_board, select_recognizer,
)

LOGGER = logging.getLogger(__name__)


class CaptureStarter(QtCore.QThread):
    ready = QtCore.Signal()
    failed = QtCore.Signal(str)

    def __init__(self, capture: PortalScreenCapture) -> None:
        super().__init__()
        self.capture = capture

    def run(self) -> None:
        try:
            self.capture.start()
        except CaptureError as exc:
            self.failed.emit(str(exc))
        else:
            self.ready.emit()


class RecognitionWorker(QtCore.QThread):
    completed = QtCore.Signal(object, object, str)
    failed = QtCore.Signal(str)

    def __init__(self, image: np.ndarray, orientation: str, template_directory: str) -> None:
        super().__init__()
        self.image, self.orientation, self.template_directory = image, orientation, template_directory

    def run(self) -> None:
        try:
            try:
                grid = detect_board_grid(self.image)
                diagnostic = "Automatic board boundary detected."
            except BoardDetectionError:
                grid = BoardGrid.manual(self.image)
                diagnostic = "Used centered manual 8×8 grid fallback."
            selection = select_recognizer(self.image, grid, self.template_directory or None)
            predictions = recognize_board(self.image, grid, selection.recognizer, self.orientation)
            if selection.profile_name:
                details = []
                if selection.similarity is not None:
                    details.append(f"palette distance {selection.similarity:.3f}")
                if selection.shape_similarity is not None:
                    details.append(f"shape similarity {selection.shape_similarity:.3f}")
                detail = f" ({'; '.join(details)})" if details else ""
                diagnostic += f" Recognition profile: {selection.profile_name}{detail}."
            else:
                diagnostic += " No matching profile; using bundled generated fallback and marking theme-dependent results uncertain."
            self.completed.emit(grid, predictions, diagnostic)
        except (BoardDetectionError, TemplateError, ValueError) as exc:
            self.failed.emit(str(exc))


class MonitoringWorker(RecognitionWorker):
    """Same proven recognizer, run off the UI thread for one captured frame."""
    completed_monitoring = QtCore.Signal(object, object, str)

    def run(self) -> None:
        try:
            try:
                grid = detect_board_grid(self.image)
                diagnostic = "Automatic board boundary detected."
            except BoardDetectionError:
                grid = BoardGrid.manual(self.image)
                diagnostic = "Used centered manual 8×8 grid fallback."
            selection = select_recognizer(self.image, grid, self.template_directory or None)
            predictions = recognize_board(self.image, grid, selection.recognizer, self.orientation)
            profile = selection.profile_name or "generated fallback / no matching profile"
            diagnostic += f" Monitoring profile: {profile}; orientation: {self.orientation}."
            self.completed_monitoring.emit(grid, predictions, diagnostic)
        except (BoardDetectionError, TemplateError, ValueError) as exc:
            self.failed.emit(str(exc))


class EngineWorker(QtCore.QThread):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, fen: str, depth: int) -> None:
        super().__init__()
        self.fen, self.depth = fen, depth
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(self) -> None:
        try:
            with ChessAnalyzer() as analyzer:
                report = analyzer.analyze(self.fen, depth=self.depth, multipv=3)
        except (EngineAnalysisError, StockfishNotFoundError, ValueError) as exc:
            if not self.cancelled:
                self.failed.emit(str(exc))
            return
        if not self.cancelled:
            self.completed.emit(report)


class BoardCanvas(QtWidgets.QWidget):
    """Internal board preview with manual-position and engine-arrow feedback."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(300, 300)
        self.predictions: dict[str, PiecePrediction] = {}
        self.orientation = "white"
        self.arrows: list[str] = []

    def set_position(self, predictions: dict[str, PiecePrediction], orientation: str) -> None:
        self.predictions, self.orientation = predictions.copy(), orientation
        self.update()

    def set_arrows(self, moves: list[str]) -> None:
        self.arrows = moves[:3]
        self.update()

    def _display_point(self, square: str, square_size: float) -> QtCore.QPointF:
        file_index, rank = "abcdefgh".index(square[0]), int(square[1]) - 1
        column = file_index if self.orientation == "white" else 7 - file_index
        row = 7 - rank if self.orientation == "white" else rank
        return QtCore.QPointF((column + .5) * square_size, (row + .5) * square_size)

    def paintEvent(self, event) -> None:
        size = min(self.width(), self.height())
        square = size / 8
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        for row in range(8):
            for column in range(8):
                painter.fillRect(column * square, row * square, square, square,
                                 QtGui.QColor("#f0d9b5") if (row + column) % 2 == 0 else QtGui.QColor("#b58863"))
        symbols = {"P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
                   "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚"}
        painter.setFont(QtGui.QFont("DejaVu Sans", int(square * .68)))
        for name, prediction in self.predictions.items():
            symbol = PIECE_TO_FEN.get(prediction.state)
            if not symbol:
                continue
            point = self._display_point(name, square)
            painter.setPen(QtGui.QColor("#d32f2f") if prediction.uncertain else QtGui.QColor("#111111"))
            painter.drawText(QtCore.QRectF(point.x() - square / 2, point.y() - square / 2, square, square),
                             QtCore.Qt.AlignmentFlag.AlignCenter, symbols[symbol])
        for move, color in zip(self.arrows, ("#28a745", "#1976d2", "#ef6c00")):
            if len(move) < 4:
                continue
            start, end = self._display_point(move[:2], square), self._display_point(move[2:4], square)
            painter.setPen(QtGui.QPen(QtGui.QColor(color), max(4, int(square * .11)), QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
            painter.drawLine(start, end)
            vector = end - start
            length = max(1.0, (vector.x() ** 2 + vector.y() ** 2) ** .5)
            unit = QtCore.QPointF(vector.x() / length, vector.y() / length)
            normal = QtCore.QPointF(-unit.y(), unit.x())
            tip = end
            back = QtCore.QPointF(end.x() - unit.x() * square * .28, end.y() - unit.y() * square * .28)
            painter.setBrush(QtGui.QColor(color)); painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawPolygon([tip, QtCore.QPointF(back.x() + normal.x() * square * .16, back.y() + normal.y() * square * .16), QtCore.QPointF(back.x() - normal.x() * square * .16, back.y() - normal.y() * square * .16)])


class PreviewWidget(QtWidgets.QLabel):
    region_selected = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__("Capture a monitor or window to begin.")
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(640, 400)
        self.setStyleSheet("background: #202124; color: #e8eaed;")
        self._frame: np.ndarray | None = None
        self._origin: QtCore.QPoint | None = None
        self._selection: QtCore.QRect | None = None

    def set_frame(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._refresh_pixmap()

    def _display_rect(self) -> QtCore.QRectF:
        if self._frame is None:
            return QtCore.QRectF()
        scale = min(self.width() / self._frame.shape[1], self.height() / self._frame.shape[0])
        width, height = self._frame.shape[1] * scale, self._frame.shape[0] * scale
        return QtCore.QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def _refresh_pixmap(self) -> None:
        if self._frame is None:
            return
        image = QtGui.QImage(
            self._frame.data, self._frame.shape[1], self._frame.shape[0], self._frame.strides[0], QtGui.QImage.Format.Format_RGB888
        ).copy()
        self.setPixmap(QtGui.QPixmap.fromImage(image).scaled(
            self.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        ))

    def source_region(self, selection: QtCore.QRectF) -> CaptureRegion | None:
        """Map a widget selection through its actual letterboxed image rect."""
        if self._frame is None:
            return None
        shown = self._display_rect()
        clipped = selection.intersected(shown)
        if clipped.width() <= 2 or clipped.height() <= 2:
            return None
        scale_x, scale_y = self._frame.shape[1] / shown.width(), self._frame.shape[0] / shown.height()
        x = max(0, round((clipped.x() - shown.x()) * scale_x))
        y = max(0, round((clipped.y() - shown.y()) * scale_y))
        width = min(self._frame.shape[1] - x, round(clipped.width() * scale_x))
        height = min(self._frame.shape[0] - y, round(clipped.height() * scale_y))
        return CaptureRegion(x, y, width, height) if width > 0 and height > 0 else None

    def resizeEvent(self, event) -> None:
        self._refresh_pixmap()
        super().resizeEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._frame is not None:
            self._origin = event.position().toPoint()
            self._selection = QtCore.QRect(self._origin, self._origin)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._origin is not None:
            self._selection = QtCore.QRect(self._origin, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._origin is not None and self._selection is not None and self._frame is not None:
            rect = self._selection
            region = self.source_region(QtCore.QRectF(rect))
            if region is not None:
                self.region_selected.emit(region)
        self._origin = None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._selection is not None and self._origin is not None:
            painter = QtGui.QPainter(self)
            painter.setPen(QtGui.QPen(QtGui.QColor("#4fc3f7"), 2))
            painter.drawRect(self._selection)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ChessVision – Wayland Screen Capture")
        self.capture = PortalScreenCapture()
        self.preview = PreviewWidget()
        self.start_button = QtWidgets.QPushButton("Start Screen Capture")
        self.stop_button = QtWidgets.QPushButton("Stop Capture")
        self.analyze_button = QtWidgets.QPushButton("Analyze Board Image")
        self.calibrate_button = QtWidgets.QPushButton("Calibrate Piece Recognition")
        self.select_button = QtWidgets.QPushButton("Select Board")
        self.export_button = QtWidgets.QPushButton("Save Current Recognition / Tracking Report")
        self.stop_button.setEnabled(False)
        self.orientation = QtWidgets.QComboBox()
        self.orientation.addItem("White at bottom", "white")
        self.orientation.addItem("Black at bottom", "black")
        self.source_selector = QtWidgets.QComboBox()
        self.source_selector.addItem("Browser", Source.BROWSER)
        self.source_selector.addItem("Screenshot", Source.SCREENSHOT)
        self.source_selector.addItem("FEN / PGN", Source.FEN_PGN)
        self.source_selector.addItem("Manual", Source.MANUAL)
        self.mode_selector = QtWidgets.QComboBox()
        for mode, title in ((AnalysisMode.OFFLINE, "Offline analysis"), (AnalysisMode.COMPLETED, "Completed game"), (AnalysisMode.TRAINING, "Permitted training / bot"), (AnalysisMode.LIVE_COMPETITIVE, "Live competitive game"), (AnalysisMode.UNKNOWN, "Uncertain / display only")):
            self.mode_selector.addItem(title, mode)
        self.browser_start_button = QtWidgets.QPushButton("Start Browser Import")
        self.browser_import_button = QtWidgets.QPushButton("Import Browser Position")
        self.text_import_button = QtWidgets.QPushButton("Import FEN / PGN")
        self.browser_import_button.setEnabled(False)
        self.browser_status = QtWidgets.QLabel("Browser not paired.")
        self.browser_status.setWordWrap(True)
        self.position_source_status = QtWidgets.QLabel("Position source: none")
        self.last_import_status = QtWidgets.QLabel("Last successful import: never")
        self.position_status = QtWidgets.QLabel("Position: incomplete — import or confirm a complete FEN.")
        self.engine_status = QtWidgets.QLabel("Engine: blocked — no valid confirmed position.")
        self.confirm_initial_button = QtWidgets.QPushButton("Confirm Initial Position")
        self.monitor_start_button = QtWidgets.QPushButton("Start Monitoring")
        self.monitor_stop_button = QtWidgets.QPushButton("Stop Monitoring")
        self.monitor_stop_button.setEnabled(False)
        self.resync_button = QtWidgets.QPushButton("Manual Resync")
        self.undo_move_button = QtWidgets.QPushButton("Undo Last Move")
        self.monitor_interval = QtWidgets.QSpinBox(); self.monitor_interval.setRange(250, 10000); self.monitor_interval.setSingleStep(250); self.monitor_interval.setValue(1000); self.monitor_interval.setSuffix(" ms")
        self.tracking_status = QtWidgets.QLabel("Tracking: ready — confirm an initial position to enable monitoring.")
        self.last_move_status = QtWidgets.QLabel("Last move: —")
        self.move_history = QtWidgets.QPlainTextEdit(); self.move_history.setReadOnly(True); self.move_history.setMaximumHeight(75)
        self.template_directory = QtWidgets.QLineEdit()
        self.template_directory.setText(str(default_template_directory()))
        self.template_directory.setToolTip("Local, user-created templates; no images are downloaded.")
        self.board_preview = QtWidgets.QLabel("No board image selected.")
        self.board_preview.setMinimumHeight(100)
        self.board_preview.setMaximumHeight(150)
        self.board_preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.editor = QtWidgets.QTableWidget(8, 8)
        self.editor.setHorizontalHeaderLabels(list("abcdefgh"))
        self.editor.setVerticalHeaderLabels([str(rank) for rank in range(8, 0, -1)])
        self.editor.setMaximumHeight(220)
        self.editor.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Maximum)
        self.undo_button = QtWidgets.QPushButton("Undo Square Edit")
        self.undo_button.setEnabled(False)
        self.metadata = {name: QtWidgets.QLineEdit() for name in ("turn", "castling", "ep", "halfmove", "fullmove")}
        placeholders = {"turn": "w or b", "castling": "KQkq or -", "ep": "- or e3", "halfmove": "0", "fullmove": "1"}
        form = QtWidgets.QFormLayout()
        for name, field in self.metadata.items():
            field.setPlaceholderText(placeholders[name])
            form.addRow(name.replace("ep", "en passant"), field)
        self.fen_display = QtWidgets.QLineEdit()
        self.fen_display.setReadOnly(True)
        self.confirm_button = QtWidgets.QPushButton("Confirm Position")
        self.analyze_position_button = QtWidgets.QPushButton("Analyze Confirmed Position")
        self.analyze_position_button.setEnabled(False)
        self.depth = QtWidgets.QSpinBox()
        self.depth.setRange(1, 30)
        self.depth.setValue(18)
        self.engine_output = QtWidgets.QPlainTextEdit()
        self.engine_output.setReadOnly(True)
        self.engine_output.setPlaceholderText("Confirm a valid position to analyze it with local Stockfish.")
        self.board_canvas = BoardCanvas()
        self.recognition_status = QtWidgets.QLabel("No board recognized yet.")
        self.recognition_status.setWordWrap(True)
        self.diagnostics = QtWidgets.QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumBlockCount(200)
        self.diagnostics.setMaximumHeight(110)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(QtWidgets.QLabel("Capture"))
        left_layout.addWidget(self.preview, 1)
        capture_controls = QtWidgets.QHBoxLayout()
        capture_controls.addWidget(self.start_button)
        capture_controls.addWidget(self.stop_button)
        left_layout.addLayout(capture_controls)
        left_layout.addWidget(self.select_button)
        left_layout.addWidget(self.board_preview)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.addWidget(QtWidgets.QLabel("Recognized Position"))
        center_layout.addWidget(self.board_canvas, 1)
        center_layout.addWidget(self.undo_button)
        center_layout.addWidget(self.editor)

        right_content = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_content)
        right_layout.addWidget(QtWidgets.QLabel("Recognition"))
        source_form = QtWidgets.QFormLayout()
        source_form.addRow("Source", self.source_selector)
        source_form.addRow("Analysis mode", self.mode_selector)
        right_layout.addLayout(source_form)
        browser_controls = QtWidgets.QHBoxLayout()
        browser_controls.addWidget(self.browser_start_button); browser_controls.addWidget(self.browser_import_button)
        right_layout.addLayout(browser_controls); right_layout.addWidget(self.browser_status)
        right_layout.addWidget(self.text_import_button)
        right_layout.addWidget(self.position_source_status)
        right_layout.addWidget(self.last_import_status)
        right_layout.addWidget(self.position_status)
        tracking_controls = QtWidgets.QGridLayout()
        tracking_controls.addWidget(self.confirm_initial_button, 0, 0)
        tracking_controls.addWidget(self.monitor_start_button, 0, 1)
        tracking_controls.addWidget(self.monitor_stop_button, 1, 0)
        tracking_controls.addWidget(self.resync_button, 1, 1)
        tracking_controls.addWidget(QtWidgets.QLabel("Interval"), 2, 0)
        tracking_controls.addWidget(self.monitor_interval, 2, 1)
        tracking_controls.addWidget(self.undo_move_button, 3, 0, 1, 2)
        right_layout.addLayout(tracking_controls)
        right_layout.addWidget(self.tracking_status); right_layout.addWidget(self.last_move_status); right_layout.addWidget(self.move_history)
        right_layout.addWidget(self.recognition_status)
        right_layout.addWidget(self.analyze_button)
        right_layout.addWidget(QtWidgets.QLabel("Engine"))
        analysis_controls = QtWidgets.QHBoxLayout()
        analysis_controls.addWidget(QtWidgets.QLabel("Depth"))
        analysis_controls.addWidget(self.depth)
        analysis_controls.addWidget(self.analyze_position_button)
        right_layout.addLayout(analysis_controls)
        right_layout.addWidget(self.engine_status)
        right_layout.addWidget(self.engine_output, 1)
        advanced = QtWidgets.QToolBox()
        metadata_widget = QtWidgets.QWidget()
        metadata_layout = QtWidgets.QVBoxLayout(metadata_widget)
        metadata_layout.addLayout(form)
        metadata_layout.addWidget(self.fen_display)
        metadata_layout.addWidget(self.confirm_button)
        advanced.addItem(metadata_widget, "Position metadata")
        template_widget = QtWidgets.QWidget()
        template_layout = QtWidgets.QVBoxLayout(template_widget)
        template_layout.addWidget(QtWidgets.QLabel("Optional local templates"))
        template_layout.addWidget(self.template_directory)
        template_layout.addWidget(self.orientation)
        template_layout.addWidget(self.calibrate_button)
        advanced.addItem(template_widget, "Recognition settings")
        debug_widget = QtWidgets.QWidget()
        debug_layout = QtWidgets.QVBoxLayout(debug_widget)
        debug_layout.addWidget(self.diagnostics)
        debug_layout.addWidget(self.export_button)
        advanced.addItem(debug_widget, "Diagnostics")
        right_layout.addWidget(advanced)
        right_scroll = QtWidgets.QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right_content)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([330, 360, 300])
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(splitter)
        self.setCentralWidget(container)
        self.setMinimumSize(750, 500)
        self.resize(1050, 700)
        self.statusBar().showMessage("Ready. GNOME will ask before sharing.")
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._refresh_preview)
        self.start_button.clicked.connect(self._start_capture)
        self.stop_button.clicked.connect(self._stop_capture)
        self.select_button.clicked.connect(lambda: self.statusBar().showMessage("Drag over the board in the capture preview."))
        self.analyze_button.clicked.connect(self._analyze_board_image)
        self.calibrate_button.clicked.connect(self._calibrate_piece_recognition)
        self.confirm_button.clicked.connect(self._confirm_position)
        self.analyze_position_button.clicked.connect(self._analyze_confirmed_position)
        self.export_button.clicked.connect(self._export_debug_report)
        self.browser_start_button.clicked.connect(self._start_browser_import)
        self.browser_import_button.clicked.connect(self._import_browser_position)
        self.text_import_button.clicked.connect(self._import_fen_pgn)
        self.confirm_initial_button.clicked.connect(self._confirm_tracking_initial)
        self.monitor_start_button.clicked.connect(self._start_monitoring)
        self.monitor_stop_button.clicked.connect(self._stop_monitoring)
        self.resync_button.clicked.connect(self._manual_resync)
        self.undo_move_button.clicked.connect(self._undo_tracked_move)
        self.mode_selector.currentIndexChanged.connect(self._update_engine_policy)
        self.preview.region_selected.connect(self._select_region)
        self.editor.cellClicked.connect(self._show_square_crop)
        self.undo_button.clicked.connect(self._undo_piece_edit)
        self._starter: CaptureStarter | None = None
        self._recognizer: RecognitionWorker | None = None
        self._monitor_worker: MonitoringWorker | None = None
        self._board_image: np.ndarray | None = None
        self._predictions: dict[str, PiecePrediction] = {}
        self._grid: BoardGrid | None = None
        self._confirmed_fen: str | None = None
        self._position_valid = False
        self._position_detail = "import or confirm a complete FEN"
        self._recognition_uncertain = False
        self._engine_worker: EngineWorker | None = None
        self._tracker = BoardTracker()
        self._monitor_region: CaptureRegion | None = None
        self._monitor_last_frame: np.ndarray | None = None
        self._monitor_frame_dimensions: tuple[int, int] | None = None
        self._last_monitor_predictions: dict[str, PiecePrediction] | None = None
        self._tracking_orientation: str | None = None
        self._monitor_timer = QtCore.QTimer(self)
        self._monitor_timer.timeout.connect(self._monitor_tick)
        self._monitoring_active = False
        self._browser_bridge: BrowserBridge | None = None
        self._active_position: Position | None = None
        self._undo_stack: list[tuple[str, PiecePrediction | None]] = []
        self._recognition_backend = "not run"
        self._populate_editor()
        for label in (self.position_status, self.engine_status, self.tracking_status, self.last_move_status):
            label.setWordWrap(True)
        self._refresh_state_controls()

    def _mark_import(self, source: str) -> None:
        self.position_source_status.setText(f"Position source: {source}")
        self.last_import_status.setText("Last successful import: " + QtCore.QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss"))

    def _set_position_state(self, valid: bool, detail: str, fen: str | None = None) -> None:
        """Position validity is intentionally independent from recognition/tracking state."""
        self._position_valid, self._position_detail = valid, detail
        self._confirmed_fen = fen if valid else None
        self.position_status.setText(f"Position: {'valid' if valid else 'incomplete / invalid'} — {detail}")
        self._refresh_state_controls()

    def _refresh_state_controls(self) -> None:
        """Derive every button from its own prerequisite; do not reuse stale UI state."""
        mode = self.mode_selector.currentData()
        fair_play_allows = isinstance(mode, AnalysisMode) and mode.engine_permitted
        engine_allowed = self._position_valid and bool(self._confirmed_fen) and fair_play_allows
        worker_running = self._engine_worker is not None and self._engine_worker.isRunning()
        self.analyze_position_button.setEnabled(engine_allowed and not worker_running)
        if not fair_play_allows:
            self.engine_status.setText("Engine: blocked — live competitive or uncertain/display-only mode.")
        elif not self._position_valid:
            self.engine_status.setText("Engine: blocked — confirm a complete valid position first.")
        else:
            self.engine_status.setText("Engine: permitted — local analysis is available.")
        self.confirm_initial_button.setEnabled(self._position_valid)
        ready_to_monitor = self._tracker.board is not None and self.capture.is_running and self._monitor_region is not None
        self.monitor_start_button.setEnabled(ready_to_monitor and not self._monitoring_active)
        self.monitor_stop_button.setEnabled(self._monitoring_active)

    def _confirm_tracking_initial(self) -> None:
        """Make the already reviewed FEN authoritative; monitoring never guesses it."""
        if not self._confirmed_fen:
            self._confirm_position()
        if not self._confirmed_fen:
            self.tracking_status.setText("Tracking: initial FEN must be complete and valid.")
            return
        try:
            update = self._tracker.confirm_initial(self._confirmed_fen)
        except ValueError as exc:
            self.tracking_status.setText(f"Tracking: {exc}")
            return
        self.tracking_status.setText("Tracking: " + update.message)
        self._tracking_orientation = self.orientation.currentData()
        self.last_move_status.setText("Last move: —")
        self.move_history.clear()
        self._refresh_state_controls()

    def _start_monitoring(self) -> None:
        if self._tracker.board is None:
            self.tracking_status.setText("Tracking: confirm the initial position before monitoring.")
            return
        if self._monitor_region is None:
            self.tracking_status.setText("Tracking: select a board region from the active capture first.")
            return
        if not self.capture.is_running:
            self.tracking_status.setText("Tracking: start screen capture first; the portal is not reopened per frame.")
            return
        self._monitor_last_frame = None
        self._monitoring_active = True
        self._monitor_timer.start(self.monitor_interval.value())
        self._refresh_state_controls()
        self.tracking_status.setText("Tracking: monitoring stable board observations.")

    def _stop_monitoring(self) -> None:
        self._monitor_timer.stop()
        self._monitoring_active = False
        self._monitor_last_frame = None
        if self._tracker.board:
            self._tracker.stop()
        self.tracking_status.setText("Tracking: stopped.")
        self._refresh_state_controls()

    def _monitor_tick(self) -> None:
        if self._monitor_worker is not None and self._monitor_worker.isRunning():
            return
        if self._monitor_region is None:
            self._tracking_capture_error("Selected board region is unavailable.")
            return
        try:
            # Take one frame and crop it exactly once. This avoids a mismatch
            # between a logged frame and a later ``selected_region`` frame.
            frame = self.capture.latest_frame()
            if frame is None:
                raise CaptureError("No captured frame is available yet.")
            self._monitor_frame_dimensions = (frame.shape[1], frame.shape[0])
            image = crop_frame(frame, self._monitor_region)
        except (CaptureError, InvalidRegionError) as exc:
            self._tracking_capture_error(str(exc)); return
        # Skip visually identical frames before invoking OpenCV/template matching.
        if self._monitor_last_frame is not None and image.shape == self._monitor_last_frame.shape:
            if float(np.mean(np.abs(image.astype(np.int16) - self._monitor_last_frame.astype(np.int16)))) < 1.0:
                return
        self._monitor_last_frame = image.copy()
        orientation = self._tracking_orientation or self.orientation.currentData()
        LOGGER.debug("Monitoring frame=%sx%s crop=x=%s y=%s w=%s h=%s orientation=%s", *self._monitor_frame_dimensions, self._monitor_region.x, self._monitor_region.y, self._monitor_region.width, self._monitor_region.height, orientation)
        self._monitor_worker = MonitoringWorker(image, orientation, self.template_directory.text().strip())
        self._monitor_worker.completed_monitoring.connect(self._monitoring_recognized)
        self._monitor_worker.failed.connect(self._monitoring_failed)
        self._monitor_worker.start()

    def _tracking_capture_error(self, detail: str) -> None:
        self._stop_monitoring()
        self.tracking_status.setText("Tracking: " + self._tracker.disconnected(f"Capture disconnected: {detail}").message)

    def _monitoring_recognized(self, grid: BoardGrid, predictions: dict, diagnostic: str) -> None:
        if not self._monitoring_active:
            return
        self._grid, self._board_image = grid, self._monitor_worker.image.copy() if self._monitor_worker else None
        self._last_monitor_predictions = predictions
        update = self._tracker.observe(predictions)
        self.tracking_status.setText(f"Tracking: {update.state.value}: {update.message}")
        self._show_tracking_diagnostics(predictions, diagnostic, update)
        if update.state is TrackingState.MOVE_CONFIRMED:
            self._predictions = predictions
            self._populate_editor(); self.board_canvas.set_position(predictions, self.orientation.currentData())
            self._confirmed_fen = self._tracker.fen
            if self._confirmed_fen:
                parts = self._confirmed_fen.split()
                for key, value in zip(("turn", "castling", "ep", "halfmove", "fullmove"), parts[1:]): self.metadata[key].setText(value)
            self.last_move_status.setText(f"Last move: {update.move_san} ({update.move_uci})")
            self.move_history.setPlainText(" ".join(self._tracker.history))
            self.board_canvas.set_arrows([])
            if self._engine_worker is not None and self._engine_worker.isRunning():
                self._engine_worker.cancel()
            self._set_position_state(True, "tracked legal move confirmed", self._confirmed_fen)

    def _monitoring_failed(self, detail: str) -> None:
        self.tracking_status.setText(f"Tracking: recognition failed; retained current position. {detail}")

    def _show_tracking_diagnostics(self, predictions: dict, diagnostic: str, update) -> None:
        """Keep actionable, local evidence for one user-triggered debug export."""
        uncertain = sum(item.uncertain for item in predictions.values())
        region = self._monitor_region
        frame = self._monitor_frame_dimensions
        rows = [
            "Monitoring observation",
            f"Capture frame: {frame[0]}×{frame[1]}" if frame else "Capture frame: unavailable",
            f"Crop: x={region.x}, y={region.y}, w={region.width}, h={region.height}" if region else "Crop: unavailable",
            "Crop application: once from the captured frame",
            f"Orientation: {self._tracking_orientation or self.orientation.currentData()} (locked when initial position was confirmed)",
            diagnostic,
            f"Uncertain squares: {uncertain}; minimum confidence: {update.confidence if update.confidence is not None else 'n/a'}",
            f"Recognized placement: {update.recognized_placement or 'n/a'}",
            f"Authoritative placement: {update.authoritative_placement or 'n/a'}",
            f"Differing squares: {update.differing_squares}; legal candidates: {update.candidate_count}",
            f"Stability: {update.stable_count}/{self._tracker.stable_observations}; rejection/decision: {update.message}",
        ]
        if update.differing_squares >= 20:
            rows.append("Likely cause: crop or board-orientation mismatch; reselect the board and reconfirm orientation.")
        self.diagnostics.setPlainText("\n".join(rows))
        LOGGER.debug("%s", " | ".join(rows))

    def _manual_resync(self) -> None:
        if not self._confirmed_fen:
            self._confirm_position()
        if not self._confirmed_fen:
            return
        try:
            update = self._tracker.resynchronize(self._confirmed_fen)
        except ValueError as exc:
            self.tracking_status.setText(f"Tracking: {exc}"); return
        self.move_history.clear(); self.last_move_status.setText("Last move: —")
        self.tracking_status.setText("Tracking: " + update.message)
        self._refresh_state_controls()

    def _undo_tracked_move(self) -> None:
        update = self._tracker.undo()
        self.tracking_status.setText("Tracking: " + update.message)
        if self._tracker.board:
            self._confirmed_fen = self._tracker.fen
            self._load_authoritative_board()
        self.move_history.setPlainText(" ".join(self._tracker.history))
        self._refresh_state_controls()

    def _load_authoritative_board(self) -> None:
        if not self._tracker.board:
            return
        symbols = {"P":"white_pawn", "N":"white_knight", "B":"white_bishop", "R":"white_rook", "Q":"white_queen", "K":"white_king", "p":"black_pawn", "n":"black_knight", "b":"black_bishop", "r":"black_rook", "q":"black_queen", "k":"black_king"}
        self._predictions = {chess.square_name(square): PiecePrediction(symbols[piece.symbol()], 1.0, False) for square, piece in self._tracker.board.piece_map().items()}
        self._populate_editor(); self.board_canvas.set_position(self._predictions, self.orientation.currentData())

    def _start_browser_import(self) -> None:
        if self._browser_bridge is None:
            self._browser_bridge = BrowserBridge()
        status = self._browser_bridge.start()
        self.browser_status.setText("Extension pairing: %s\nToken: %s\nUse the extension popup, then click Import Browser Position." % (f"http://127.0.0.1:{status.port}", status.token))
        self.browser_import_button.setEnabled(True)
        self.statusBar().showMessage("Browser import listening on localhost only.")

    def _import_browser_position(self) -> None:
        position = self._browser_bridge.get_position() if self._browser_bridge else None
        if position is None:
            self.statusBar().showMessage("No browser position received yet. Press Import in the extension first.")
            return
        self._apply_position(position)

    def _apply_position(self, position: Position) -> None:
        symbols = {"P":"white_pawn", "N":"white_knight", "B":"white_bishop", "R":"white_rook", "Q":"white_queen", "K":"white_king", "p":"black_pawn", "n":"black_knight", "b":"black_bishop", "r":"black_rook", "q":"black_queen", "k":"black_king"}
        predictions: dict[str, PiecePrediction] = {}
        try:
            board = chess.Board.empty(); board.set_board_fen(position.placement)
        except ValueError as exc:
            self.statusBar().showMessage(f"Rejected browser position: {exc}"); return
        for square, piece in board.piece_map().items():
            predictions[chess.square_name(square)] = PiecePrediction(symbols[piece.symbol()], position.confidence or 0.0, (position.confidence or 0) < .9)
        self._predictions = predictions; self._undo_stack.clear(); self._active_position = position
        self._last_monitor_predictions = None
        self.orientation.setCurrentIndex(0 if position.orientation == "white" else 1)
        metadata = position.metadata
        for key, value in (("turn", metadata.turn), ("castling", metadata.castling), ("ep", metadata.ep_square), ("halfmove", metadata.halfmove_clock), ("fullmove", metadata.fullmove_number)):
            self.metadata[key].setText("" if value is None else str(value))
        self._populate_editor(); self.board_canvas.set_position(predictions, position.orientation)
        valid, warnings = position.validate()
        self._recognition_uncertain = False
        # A complete FEN/PGN is authoritative and must not inherit a prior
        # screenshot's uncertain tracker state.
        self._tracker = BoardTracker()
        if self._monitoring_active:
            self._stop_monitoring()
        if valid and position.fen():
            self.fen_display.setText(position.fen() or "")
            self._set_position_state(True, "imported FEN/PGN validated", position.fen())
            self.tracking_status.setText("Tracking: ready — imported position awaits Confirm Initial Position.")
        else:
            self._set_position_state(False, "imported position metadata is incomplete")
            self.tracking_status.setText("Tracking: waiting — imported position needs complete metadata.")
        self.recognition_status.setText("Import. " + " ".join(warnings or ("Position validated.",)))
        source_indexes = {Source.BROWSER: 0, Source.SCREENSHOT: 1, Source.FEN_PGN: 2, Source.MANUAL: 3}
        self.source_selector.setCurrentIndex(source_indexes[position.source]); self._update_engine_policy()
        self._mark_import(position.source.value)
        self.statusBar().showMessage("Position imported. Confirm Initial Position before tracking; engine availability is shown separately.")

    def _import_fen_pgn(self) -> None:
        text, ok = QtWidgets.QInputDialog.getMultiLineText(self, "Import FEN or PGN", "Paste a complete FEN or PGN:")
        if not ok or not text.strip():
            return
        try:
            position = Position.from_fen(text.strip()) if len(text.strip().split()) == 6 and "/" in text else Position.from_pgn(text)
        except ValueError as exc:
            self.statusBar().showMessage(f"FEN/PGN import failed: {exc}")
            return
        self._apply_position(position)

    def _update_engine_policy(self) -> None:
        self._refresh_state_controls()
        mode = self.mode_selector.currentData()
        if not (isinstance(mode, AnalysisMode) and mode.engine_permitted):
            self.engine_output.setPlainText("Engine recommendations are disabled for live competitive or uncertain/display-only mode. Board display/import remains available.")

    def _start_capture(self) -> None:
        self.start_button.setEnabled(False)
        self.statusBar().showMessage("Waiting for GNOME screen-sharing permission…")
        self._starter = CaptureStarter(self.capture)
        self._starter.ready.connect(self._capture_started)
        self._starter.failed.connect(self._capture_failed)
        self._starter.start()

    def _capture_started(self) -> None:
        self.stop_button.setEnabled(True)
        self.timer.start(33)
        self.statusBar().showMessage("Capture started. Drag over the chessboard to select it.")
        self._refresh_state_controls()

    def _capture_failed(self, message: str) -> None:
        self.start_button.setEnabled(True)
        self.statusBar().showMessage(message)

    def _stop_capture(self) -> None:
        if self._monitor_timer.isActive():
            self._stop_monitoring()
        self.timer.stop()
        self.capture.stop()
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(True)
        self.statusBar().showMessage("Capture stopped.")
        self._refresh_state_controls()

    def _refresh_preview(self) -> None:
        frame = self.capture.latest_frame()
        if frame is not None:
            self.preview.set_frame(frame)

    def _select_region(self, region: CaptureRegion) -> None:
        try:
            image = self.capture.selected_region(region)
        except CaptureError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self.statusBar().showMessage(
            f"Selected board candidate: {image.shape[1]} × {image.shape[0]} pixels."
        )
        self._board_image = image
        self._last_monitor_predictions = None
        self._monitor_region = region
        self._refresh_state_controls()
        qimage = QtGui.QImage(image.data, image.shape[1], image.shape[0], image.strides[0], QtGui.QImage.Format.Format_RGB888).copy()
        self.board_preview.setPixmap(QtGui.QPixmap.fromImage(qimage).scaledToHeight(180, QtCore.Qt.TransformationMode.SmoothTransformation))
        self.recognition_status.setText("Board crop selected. Choose Analyze Board Image.")
        self.diagnostics.setPlainText(
            f"Selected board crop: {image.shape[1]} × {image.shape[0]} RGB pixels\n"
            f"Source crop: x={region.x}, y={region.y}, w={region.width}, h={region.height}"
        )

    def _analyze_board_image(self) -> None:
        if self._board_image is None:
            self.statusBar().showMessage("Select a board-sized capture region first.")
            return
        self.analyze_button.setEnabled(False)
        self._recognizer = RecognitionWorker(self._board_image.copy(), self.orientation.currentData(), self.template_directory.text().strip())
        self._recognizer.completed.connect(self._recognition_complete)
        self._recognizer.failed.connect(self._recognition_failed)
        self._recognizer.start()

    def _recognition_complete(self, grid: BoardGrid, predictions: dict, diagnostic: str) -> None:
        self._grid, self._predictions = grid, predictions
        self._last_monitor_predictions = None
        self._undo_stack.clear()
        self.undo_button.setEnabled(False)
        self._recognition_backend = diagnostic
        self.source_selector.setCurrentIndex(1)
        self._mark_import(Source.SCREENSHOT.value)
        self._populate_editor()
        self.board_canvas.set_position(self._predictions, self.orientation.currentData())
        self.analyze_button.setEnabled(True)
        uncertain = sum(item.uncertain for item in predictions.values())
        self._recognition_uncertain = bool(uncertain)
        warning = recognition_warning(predictions)
        message = f"{diagnostic} {uncertain} uncertain squares; review highlighted tooltips before confirming."
        if warning:
            message = f"Recognition warning: {warning}"
        self.statusBar().showMessage(message)
        self.recognition_status.setText(message)
        # A screenshot is a pending visual observation. It never replaces an
        # already confirmed imported/tracked board until the user confirms it.
        if self._position_valid:
            self.position_status.setText("Position: valid — authoritative confirmed position retained; screenshot awaits confirmation.")
        else:
            self._set_position_state(False, "screenshot requires metadata and confirmation")
        backend = self._recognition_backend_name()
        rows = [f"Backend: {backend}", f"Grid: {grid.source}, {grid.size}px", f"Uncertain squares: {uncertain}"]
        rows.extend(f"{name}: {item.state} ({item.confidence:.0%}){' uncertain' if item.uncertain else ''}" for name, item in sorted(predictions.items()))
        self.diagnostics.setPlainText("\n".join(rows))

    def _recognition_failed(self, message: str) -> None:
        self.analyze_button.setEnabled(True)
        text = f"Board recognition failed: {message}"
        self.statusBar().showMessage(text)
        self.recognition_status.setText(text)
        self.diagnostics.setPlainText(text)

    def _export_debug_report(self) -> None:
        report_predictions = self._last_monitor_predictions or self._predictions
        if self._board_image is None or self._grid is None or not report_predictions:
            self.statusBar().showMessage("Analyze a board before exporting recognition diagnostics.")
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Export Recognition Debug Report")
        if not directory:
            return
        try:
            backend = self._recognition_backend_name()
            report = export_debug_report(directory, self._board_image, self._grid, report_predictions, backend)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(f"Debug export failed: {exc}")
            return
        self.statusBar().showMessage(f"Recognition debug report saved to {report}.")

    def _recognition_backend_name(self) -> str:
        return self._recognition_backend

    def _calibrate_piece_recognition(self) -> None:
        if self._board_image is None:
            self.statusBar().showMessage("Select a full standard starting-position board image first.")
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Calibrate Piece Recognition",
            "Is the selected board a complete standard starting position with the selected orientation? "
            "Calibration labels its squares and saves only local templates.",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose local template directory", self.template_directory.text()
        )
        if not directory:
            return
        try:
            try:
                grid = detect_board_grid(self._board_image)
            except BoardDetectionError:
                grid = BoardGrid.manual(self._board_image)
            report = calibrate_starting_position(
                self._board_image, grid, self.orientation.currentData(), directory
            )
        except (BoardDetectionError, TemplateError, ValueError) as exc:
            self.statusBar().showMessage(f"Calibration failed: {exc}")
            return
        self.template_directory.setText(str(report.directory))
        self._grid = grid
        missing = len(report.missing_square_color_combinations)
        self.statusBar().showMessage(
            f"Saved {sum(report.sample_counts.values())} local samples. "
            f"{missing} piece/square-color combinations are not represented; add more templates for this theme."
        )

    def _populate_editor(self) -> None:
        for row in range(8):
            for column in range(8):
                name = f"{'abcdefgh'[column]}{8 - row}"
                combo = QtWidgets.QComboBox()
                combo.addItems(PIECE_STATES)
                prediction = self._predictions.get(name)
                if prediction is not None:
                    combo.setCurrentText(prediction.state)
                    combo.setToolTip(f"confidence: {prediction.confidence:.0%}" + (" (uncertain)" if prediction.uncertain else ""))
                combo.currentTextChanged.connect(lambda value, square=name: self._manual_piece(square, value))
                self.editor.setCellWidget(row, column, combo)

    def _manual_piece(self, square: str, state: str) -> None:
        previous = self._predictions.get(square)
        if previous is not None and previous.state == state:
            return
        self._undo_stack.append((square, previous))
        self.undo_button.setEnabled(True)
        self._predictions[square] = PiecePrediction(state, 1.0, False)
        self.board_canvas.set_position(self._predictions, self.orientation.currentData())
        self.source_selector.setCurrentIndex(3)
        self._mark_import(Source.MANUAL.value)
        self._recognition_uncertain = False
        self._set_position_state(False, "manual edits require confirmation before analysis")

    def _undo_piece_edit(self) -> None:
        if not self._undo_stack:
            return
        square, previous = self._undo_stack.pop()
        if previous is None:
            self._predictions.pop(square, None)
        else:
            self._predictions[square] = previous
        self.undo_button.setEnabled(bool(self._undo_stack))
        self._populate_editor()
        self.board_canvas.set_position(self._predictions, self.orientation.currentData())
        self.statusBar().showMessage(f"Restored {square} to its previous recognition result.")

    def _show_square_crop(self, row: int, column: int) -> None:
        """Show the original source square while the editor is being reviewed."""
        if self._board_image is None or self._grid is None:
            return
        square = self._grid.extract_square(self._board_image, row, column)
        image = QtGui.QImage(
            square.data, square.shape[1], square.shape[0], square.strides[0],
            QtGui.QImage.Format.Format_RGB888,
        ).copy()
        self.board_preview.setPixmap(QtGui.QPixmap.fromImage(image).scaledToHeight(
            150, QtCore.Qt.TransformationMode.SmoothTransformation,
        ))
        self.statusBar().showMessage(
            f"Reviewing {'abcdefgh'[column]}{8 - row}: original captured square shown at left."
        )

    def _confirm_position(self) -> None:
        try:
            metadata = FenMetadata(
                self.metadata["turn"].text().strip() or None,
                self.metadata["castling"].text().strip() or None,
                self.metadata["ep"].text().strip() or None,
                int(self.metadata["halfmove"].text()) if self.metadata["halfmove"].text() else None,
                int(self.metadata["fullmove"].text()) if self.metadata["fullmove"].text() else None,
            )
            fen = build_fen(self._predictions, metadata)
            validation = validate_fen(fen)
            self.fen_display.setText(fen)
            self.statusBar().showMessage(validation.message)
            self._recognition_uncertain = False
            if validation.position_valid:
                if self._monitoring_active:
                    self._stop_monitoring()
                self._tracker = BoardTracker()
                self.tracking_status.setText("Tracking: ready — position confirmed; click Confirm Initial Position to track it.")
                self._set_position_state(True, "position confirmed and legal", fen)
            else:
                self._set_position_state(False, validation.message)
        except (FenBuildError, ValueError) as exc:
            self._set_position_state(False, str(exc))
            self.statusBar().showMessage(f"Position not confirmed: {exc}")

    def _analyze_confirmed_position(self) -> None:
        if not self._position_valid or not self._confirmed_fen:
            self.statusBar().showMessage("Engine analysis needs a complete valid confirmed position.")
            return
        if not self.mode_selector.currentData().engine_permitted:
            self.statusBar().showMessage("Engine assistance is disabled in this analysis mode.")
            return
        if not self._confirmed_fen:
            self.statusBar().showMessage("Confirm a valid FEN position before analysis.")
            return
        if self._engine_worker is not None and self._engine_worker.isRunning():
            self._engine_worker.cancel()
        self.analyze_position_button.setEnabled(False)
        self.engine_output.setPlainText("Analyzing locally with Stockfish…")
        self._engine_worker = EngineWorker(self._confirmed_fen, self.depth.value())
        self._engine_worker.completed.connect(self._engine_completed)
        self._engine_worker.failed.connect(self._engine_failed)
        self._engine_worker.start()

    def _engine_completed(self, report) -> None:
        self._refresh_state_controls()
        if report.terminal:
            self.engine_output.setPlainText(f"Game over: {report.termination} ({report.result})")
            self.board_canvas.set_arrows([])
            return
        rows, arrows = [], []
        for number, line in enumerate(report.variations, 1):
            evaluation = f"mate {line.mate_distance}" if line.mate_distance is not None else f"{line.evaluation_centipawns / 100:+.2f}" if line.evaluation_centipawns is not None else "unknown"
            rows.append(f"{number}. {line.move_san} ({line.move_uci})  {evaluation}\n   {' '.join(line.principal_variation_san)}")
            arrows.append(line.move_uci)
        self.engine_output.setPlainText("\n".join(rows) or "Stockfish returned no variations.")
        self.board_canvas.set_arrows(arrows)
        self.statusBar().showMessage("Stockfish analysis complete. Green, blue, and orange arrows show MultiPV 1–3.")

    def _engine_failed(self, message: str) -> None:
        self._refresh_state_controls()
        self.engine_output.setPlainText(f"Stockfish analysis failed: {message}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._engine_worker is not None and self._engine_worker.isRunning():
            self._engine_worker.cancel()
            self._engine_worker.wait(2000)
        self._stop_capture()
        if self._browser_bridge is not None:
            self._browser_bridge.close()
        event.accept()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    available = app.primaryScreen().availableGeometry()
    window.resize(min(1050, available.width()), min(700, available.height()))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
