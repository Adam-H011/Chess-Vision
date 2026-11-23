"""Conservative continuous move tracking over existing recognition observations.

Recognition supplies only a board placement.  ``BoardTracker`` never uses that
placement to rebuild its chess state: it accepts it only when it uniquely
matches a legal successor of the authoritative :class:`chess.Board`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

import chess


class Prediction(Protocol):
    state: str
    confidence: float
    uncertain: bool


class TrackingState(str, Enum):
    WAITING_INITIAL = "waiting-initial-confirmation"
    MONITORING = "monitoring"
    CANDIDATE = "candidate-move"
    MOVE_CONFIRMED = "move-confirmed"
    UNCERTAIN = "uncertain-position"
    DISCONNECTED = "capture-disconnected"


@dataclass(frozen=True)
class TrackingUpdate:
    state: TrackingState
    message: str
    move_uci: str | None = None
    move_san: str | None = None
    confidence: float | None = None
    recognized_placement: str | None = None
    authoritative_placement: str | None = None
    differing_squares: int = 0
    candidate_count: int = 0
    stable_count: int = 0
    uncertain_recognition: bool = False


_STATE_TO_SYMBOL = {
    "white_pawn": "P", "white_knight": "N", "white_bishop": "B", "white_rook": "R", "white_queen": "Q", "white_king": "K",
    "black_pawn": "p", "black_knight": "n", "black_bishop": "b", "black_rook": "r", "black_queen": "q", "black_king": "k",
}


def placement_from_predictions(predictions: Mapping[str, Prediction]) -> tuple[str, float, bool]:
    """Convert all 64 classified squares into placement; retain uncertainty."""
    rows: list[str] = []
    confidences: list[float] = []
    uncertain = False
    for rank in range(8, 0, -1):
        row, run = [], 0
        for file_name in "abcdefgh":
            item = predictions.get(f"{file_name}{rank}")
            state = item.state if item else "empty"
            if item:
                confidences.append(item.confidence)
                uncertain |= item.uncertain
            symbol = _STATE_TO_SYMBOL.get(state)
            if symbol:
                if run: row.append(str(run)); run = 0
                row.append(symbol)
            elif state == "empty":
                run += 1
            else:
                uncertain = True; run += 1
        if run: row.append(str(run))
        rows.append("".join(row))
    return "/".join(rows), (min(confidences) if confidences else 0.0), uncertain


def placement_difference_count(left: str, right: str) -> int:
    """Count logical-square differences between two board-FEN placements."""
    def squares(placement: str) -> list[str]:
        result: list[str] = []
        for rank in placement.split("/"):
            for cell in rank:
                result.extend(["."] * int(cell) if cell.isdigit() else [cell])
        if len(result) != 64:
            raise ValueError("Invalid board placement")
        return result
    try:
        return sum(a != b for a, b in zip(squares(left), squares(right)))
    except ValueError:
        return 64


class BoardTracker:
    """Require repeated, confident observations before applying one legal move."""
    def __init__(self, *, stable_observations: int = 2, min_confidence: float = .70) -> None:
        if stable_observations < 2:
            raise ValueError("stable_observations must be at least 2")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        self.stable_observations, self.min_confidence = stable_observations, min_confidence
        self.board: chess.Board | None = None
        self.state = TrackingState.WAITING_INITIAL
        self.history: list[str] = []
        self._candidate_placement: str | None = None
        self._candidate_count = 0

    @property
    def fen(self) -> str | None:
        # Standard FEN records the target after every double pawn push, even
        # where no opposing pawn can legally capture it.
        return self.board.fen(en_passant="fen") if self.board else None

    def confirm_initial(self, fen: str) -> TrackingUpdate:
        board = chess.Board(fen)
        if not board.is_valid():
            raise ValueError(f"Initial position is invalid (status {board.status()}).")
        self.board = board
        self.history.clear(); self._candidate_placement = None; self._candidate_count = 0
        self.state = TrackingState.MONITORING
        return TrackingUpdate(self.state, "Initial position confirmed; monitoring can start.")

    def stop(self) -> None:
        self._candidate_placement = None; self._candidate_count = 0
        if self.board: self.state = TrackingState.WAITING_INITIAL

    def disconnected(self, detail: str = "Capture disconnected.") -> TrackingUpdate:
        self.state = TrackingState.DISCONNECTED
        self._candidate_placement = None; self._candidate_count = 0
        return TrackingUpdate(self.state, detail)

    def observe(self, predictions: Mapping[str, Prediction]) -> TrackingUpdate:
        placement, confidence, uncertain = placement_from_predictions(predictions)
        return self.observe_placement(placement, confidence=confidence, uncertain=uncertain)

    def observe_placement(self, placement: str, *, confidence: float = 1.0, uncertain: bool = False) -> TrackingUpdate:
        if self.board is None:
            self.state = TrackingState.WAITING_INITIAL
            return TrackingUpdate(self.state, "Confirm a complete initial FEN before monitoring.")
        authoritative = self.board.board_fen()
        differences = placement_difference_count(placement, authoritative)
        # An exact placement match is a valid heartbeat. Recognition confidence
        # can be low for background/empty-square template scores, but it must
        # never turn an unchanged board into a phantom move or tracking error.
        if differences == 0:
            self.state = TrackingState.MONITORING
            self._candidate_placement = None; self._candidate_count = 0
            return TrackingUpdate(self.state, "Board matches the authoritative position; no move detected.", confidence=confidence,
                                  recognized_placement=placement, authoritative_placement=authoritative,
                                  differing_squares=0, uncertain_recognition=uncertain)
        if uncertain or confidence < self.min_confidence:
            self.state = TrackingState.UNCERTAIN
            self._candidate_placement = None; self._candidate_count = 0
            return TrackingUpdate(self.state, "Recognition is uncertain for a changed board; retained the tracked position.", confidence=confidence,
                                  recognized_placement=placement, authoritative_placement=authoritative,
                                  differing_squares=differences, uncertain_recognition=True)
        candidates: list[chess.Move] = []
        for move in self.board.legal_moves:
            next_board = self.board.copy(stack=False); next_board.push(move)
            if next_board.board_fen() == placement:
                candidates.append(move)
        if len(candidates) != 1:
            self.state = TrackingState.UNCERTAIN
            self._candidate_placement = None; self._candidate_count = 0
            reason = "does not match any legal move" if not candidates else "matches multiple legal moves"
            return TrackingUpdate(self.state, f"Recognized board {reason}; retained the tracked position.", confidence=confidence,
                                  recognized_placement=placement, authoritative_placement=authoritative,
                                  differing_squares=differences, candidate_count=len(candidates))
        move = candidates[0]
        if placement == self._candidate_placement:
            self._candidate_count += 1
        else:
            self._candidate_placement, self._candidate_count = placement, 1
        san = self.board.san(move)
        if self._candidate_count < self.stable_observations:
            self.state = TrackingState.CANDIDATE
            return TrackingUpdate(self.state, f"Candidate {san}; waiting for stable observation ({self._candidate_count}/{self.stable_observations}).", move.uci(), san, confidence,
                                  placement, authoritative, differences, 1, self._candidate_count)
        self.board.push(move); self.history.append(san)
        self._candidate_placement = None; self._candidate_count = 0
        self.state = TrackingState.MOVE_CONFIRMED
        return TrackingUpdate(self.state, f"Confirmed {san}.", move.uci(), san, confidence,
                              placement, authoritative, differences, 1, self.stable_observations)

    def undo(self) -> TrackingUpdate:
        if self.board is None or not self.history:
            return TrackingUpdate(self.state, "No confirmed tracked move to undo.")
        san = self.history.pop(); self.board.pop()
        self.state = TrackingState.MONITORING
        self._candidate_placement = None; self._candidate_count = 0
        return TrackingUpdate(self.state, f"Undid {san}.")

    def resynchronize(self, fen: str) -> TrackingUpdate:
        """Explicit user action only; never automatic after disagreement."""
        update = self.confirm_initial(fen)
        return TrackingUpdate(update.state, "Manually resynchronized authoritative position.")
