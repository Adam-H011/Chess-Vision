"""Change-first visual move inference for a confirmed chessboard.

Unlike full-board recognition this only asks which squares visually changed.
It deliberately knows nothing about piece identities in pixels; python-chess
supplies legal moves and their affected logical squares.
"""
from __future__ import annotations

from dataclasses import dataclass

import chess
import cv2
import numpy as np

from .board_detection import BoardGrid, Orientation
from .piece_recognition import foreground_mask


@dataclass(frozen=True)
class VisualCandidate:
    move_uci: str
    score: float
    affected_squares: tuple[str, ...]


@dataclass(frozen=True)
class ChangeObservation:
    change_scores: dict[str, float]
    changed_squares: tuple[str, ...]
    candidates: tuple[VisualCandidate, ...]
    decision: str
    geometry_warning: str | None = None
    baseline_feature_pixels: int = 0
    current_feature_pixels: int = 0


def _affected_squares(board: chess.Board, move: chess.Move) -> tuple[str, ...]:
    squares = {chess.square_name(move.from_square), chess.square_name(move.to_square)}
    if board.is_castling(move):
        rank = chess.square_rank(move.from_square)
        if chess.square_file(move.to_square) > chess.square_file(move.from_square):
            squares.update((chess.square_name(chess.square(7, rank)), chess.square_name(chess.square(5, rank))))
        else:
            squares.update((chess.square_name(chess.square(0, rank)), chess.square_name(chess.square(3, rank))))
    if board.is_en_passant(move):
        squares.add(chess.square_name(chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))))
    return tuple(sorted(squares))


class ChangeFirstTracker:
    """Visual baseline plus bounded legal-move ranking, with no piece classifier."""
    def __init__(self, *, change_threshold: float = .075, minimum_score: float = .45, separation: float = .10) -> None:
        self.change_threshold = change_threshold
        self.minimum_score = minimum_score
        self.separation = separation
        self._baseline: dict[str, np.ndarray] | None = None
        self._baseline_image: np.ndarray | None = None
        self.orientation: Orientation | None = None
        self.geometry: BoardGrid | None = None

    @property
    def ready(self) -> bool:
        return self._baseline is not None

    @property
    def baseline_image(self) -> np.ndarray | None:
        return None if self._baseline_image is None else self._baseline_image.copy()

    @staticmethod
    def _masks(image: np.ndarray, grid: BoardGrid, orientation: Orientation) -> dict[str, np.ndarray]:
        result: dict[str, np.ndarray] = {}
        for row in range(8):
            for col in range(8):
                square = grid.extract_square(image, row, col)
                resized = cv2.resize(square[:, :, :3], (64, 64), interpolation=cv2.INTER_AREA)
                mask, _ = foreground_mask(resized)
                # Highlights, dots and coordinate labels live at edges; retain
                # only the centre, where a chess piece silhouette belongs.
                mask[:5] = 0; mask[-5:] = 0; mask[:, :5] = 0; mask[:, -5:] = 0
                # Foreground segmentation may be empty for low-contrast themed
                # pieces. Central edges provide a second, local visual signal
                # without responding to whole-square last-move highlights.
                gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
                edges = cv2.Canny(gray, 28, 80)
                edges[:5] = 0; edges[-5:] = 0; edges[:, :5] = 0; edges[:, -5:] = 0
                result[grid.square_name(row, col, orientation)] = np.maximum(mask, edges)
        return result

    def set_baseline(self, image: np.ndarray, grid: BoardGrid, orientation: Orientation) -> None:
        self._baseline = self._masks(image, grid, orientation)
        self._baseline_image = image[:, :, :3].copy()
        self.orientation, self.geometry = orientation, grid

    def observe(self, image: np.ndarray, grid: BoardGrid, board: chess.Board) -> ChangeObservation:
        if self._baseline is None or self.orientation is None:
            return ChangeObservation({}, (), (), "No confirmed visual baseline is available.")
        # A different aspect/grid indicates re-selection or layout movement.
        if abs(grid.square_width - self.geometry.square_width) / max(self.geometry.square_width, 1) > .18 or abs(grid.square_height - self.geometry.square_height) / max(self.geometry.square_height, 1) > .18:
            return ChangeObservation({}, (), (), "Board geometry changed too much; pause and reselect the board.", "board scale changed")
        current = self._masks(image, grid, self.orientation)
        scores: dict[str, float] = {}
        for square, before in self._baseline.items():
            after = current[square]
            union = np.logical_or(before > 0, after > 0).sum()
            scores[square] = float(np.logical_xor(before > 0, after > 0).sum() / max(union, 1))
        changed = tuple(sorted(square for square, score in scores.items() if score >= self.change_threshold))
        baseline_pixels = sum(int((feature > 0).sum()) for feature in self._baseline.values())
        current_pixels = sum(int((feature > 0).sum()) for feature in current.values())
        if not changed:
            detail = "Board is visually stable; no move detected."
            if baseline_pixels == 0 or current_pixels == 0:
                detail = "No usable piece features were found; visual comparison is insufficient. Re-select the board or save diagnostics."
            return ChangeObservation(scores, changed, (), detail, baseline_feature_pixels=baseline_pixels, current_feature_pixels=current_pixels)
        candidates: list[VisualCandidate] = []
        changed_set = set(changed)
        for move in board.legal_moves:
            affected = set(_affected_squares(board, move))
            expected = float(np.mean([scores[square] for square in affected]))
            # Compare a candidate with the squares that contain move-sized
            # evidence. Averaging over the other 60+ quiet squares diluted
            # the penalty for a candidate that explained only half of a
            # castling move, making it appear almost tied with castling.
            unexpected_squares = changed_set - affected
            unexpected = float(np.mean([scores[square] for square in unexpected_squares])) if unexpected_squares else 0.0
            # Reward change where the move predicts it and penalize widespread
            # visual disturbance such as a drag animation or bad crop.
            candidates.append(VisualCandidate(move.uci(), expected - .65 * unexpected, tuple(sorted(affected))))
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        if not candidates or candidates[0].score < self.minimum_score:
            return ChangeObservation(scores, changed, tuple(candidates[:8]), "Visual changes do not strongly support one legal move.", baseline_feature_pixels=baseline_pixels, current_feature_pixels=current_pixels)
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < self.separation:
            return ChangeObservation(scores, changed, tuple(candidates[:8]), "Visual change evidence is ambiguous; retained the previous position.", baseline_feature_pixels=baseline_pixels, current_feature_pixels=current_pixels)
        return ChangeObservation(scores, changed, tuple(candidates[:8]), f"Visual evidence favors {candidates[0].move_uci}.", baseline_feature_pixels=baseline_pixels, current_feature_pixels=current_pixels)

    def accept_current(self, image: np.ndarray, grid: BoardGrid) -> None:
        """Update baseline only after a tracked move was confirmed."""
        assert self.orientation is not None
        self.set_baseline(image, grid, self.orientation)
