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
    RECOVERY_PENDING = "recovery-confirmation-required"


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
    differing_square_names: tuple[str, ...] = ()
    proposed_sequence: tuple[str, ...] = ()
    recovery_requires_confirmation: bool = False
    rejection_reason: str | None = None
    candidate_summaries: tuple[str, ...] = ()


_STATE_TO_SYMBOL = {
    "white_pawn": "P", "white_knight": "N", "white_bishop": "B", "white_rook": "R", "white_queen": "Q", "white_king": "K",
    "black_pawn": "p", "black_knight": "n", "black_bishop": "b", "black_rook": "r", "black_queen": "q", "black_king": "k",
}
_SYMBOL_TO_STATE = {symbol: state for state, symbol in _STATE_TO_SYMBOL.items()}


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


def placement_differences(left: str, right: str) -> tuple[str, ...]:
    """Logical differing squares, independent of the displayed orientation."""
    def mapping(placement: str) -> dict[str, str]:
        output: dict[str, str] = {}
        ranks = placement.split("/")
        if len(ranks) != 8:
            raise ValueError("Invalid board placement")
        for row, rank in enumerate(ranks):
            file_index = 0
            for cell in rank:
                if cell.isdigit(): file_index += int(cell)
                else:
                    if file_index >= 8: raise ValueError("Invalid board placement")
                    output[f"{'abcdefgh'[file_index]}{8-row}"] = cell; file_index += 1
            if file_index != 8: raise ValueError("Invalid board placement")
        return output
    try:
        one, two = mapping(left), mapping(right)
        return tuple(square for square in (f"{file}{rank}" for rank in range(1, 9) for file in "abcdefgh") if one.get(square, ".") != two.get(square, "."))
    except ValueError:
        return ()


def _affected_squares_for_move(board: chess.Board, move: chess.Move) -> tuple[str, ...]:
    squares = {chess.square_name(move.from_square), chess.square_name(move.to_square)}
    if board.is_castling(move):
        rank = chess.square_rank(move.from_square)
        rook_from, rook_to = (7, 5) if chess.square_file(move.to_square) > chess.square_file(move.from_square) else (0, 3)
        squares.update((chess.square_name(chess.square(rook_from, rank)), chess.square_name(chess.square(rook_to, rank))))
    if board.is_en_passant(move):
        squares.add(chess.square_name(chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))))
    return tuple(sorted(squares))


class BoardTracker:
    """Require repeated, confident observations before applying one legal move."""
    def __init__(self, *, stable_observations: int = 2, min_confidence: float = .70,
                 max_recovery_plies: int = 3, max_recovery_nodes: int = 1500, max_piece_errors: int = 2) -> None:
        if stable_observations < 2:
            raise ValueError("stable_observations must be at least 2")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 2 <= max_recovery_plies <= 3 or max_recovery_nodes < 1 or not 0 <= max_piece_errors <= 2:
            raise ValueError("Recovery limits must use 2–3 plies, positive nodes, and 0–2 piece errors")
        self.stable_observations, self.min_confidence = stable_observations, min_confidence
        self.max_recovery_plies, self.max_recovery_nodes, self.max_piece_errors = max_recovery_plies, max_recovery_nodes, max_piece_errors
        self.board: chess.Board | None = None
        self.state = TrackingState.WAITING_INITIAL
        self.history: list[str] = []
        self._candidate_placement: str | None = None
        self._candidate_count = 0
        self._pending_sequences: list[tuple[chess.Move, ...]] = []
        self._pending_observed: str | None = None

    @property
    def fen(self) -> str | None:
        # Standard FEN records the target after every double pawn push, even
        # where no opposing pawn can legally capture it.
        return self.board.fen(en_passant="fen") if self.board else None

    @property
    def can_confirm_proposed_sequence(self) -> bool:
        return len(self._pending_sequences) == 1

    @property
    def needs_stability_observation(self) -> bool:
        """A duplicate frame is evidence while a move/recovery is pending."""
        return self.state is TrackingState.CANDIDATE

    def confirm_initial(self, fen: str) -> TrackingUpdate:
        board = chess.Board(fen)
        if not board.is_valid():
            raise ValueError(f"Initial position is invalid (status {board.status()}).")
        self.board = board
        self.history.clear(); self._candidate_placement = None; self._candidate_count = 0
        self._pending_sequences.clear(); self._pending_observed = None
        self.state = TrackingState.MONITORING
        return TrackingUpdate(self.state, "Initial position confirmed; monitoring can start.")

    def stop(self) -> None:
        self._candidate_placement = None; self._candidate_count = 0
        self._pending_sequences.clear(); self._pending_observed = None
        if self.board: self.state = TrackingState.WAITING_INITIAL

    def disconnected(self, detail: str = "Capture disconnected.") -> TrackingUpdate:
        self.state = TrackingState.DISCONNECTED
        self._candidate_placement = None; self._candidate_count = 0
        return TrackingUpdate(self.state, detail)

    def observe(self, predictions: Mapping[str, Prediction]) -> TrackingUpdate:
        placement, confidence, uncertain = placement_from_predictions(predictions)
        return self.observe_placement(placement, confidence=confidence, uncertain=uncertain)

    def observe_visual_move(self, move_uci: str, score: float) -> TrackingUpdate:
        """Apply only stable, legal change-first visual evidence."""
        if self.board is None:
            return TrackingUpdate(TrackingState.WAITING_INITIAL, "Confirm an initial position before tracking.")
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError:
            move = chess.Move.null()
        if move not in self.board.legal_moves:
            self.state = TrackingState.UNCERTAIN
            return TrackingUpdate(self.state, "Visual candidate is not legal in the tracked position.", confidence=score, rejection_reason="illegal visual candidate")
        token = f"visual:{move_uci}"
        if token == self._candidate_placement: self._candidate_count += 1
        else: self._candidate_placement, self._candidate_count = token, 1
        san = self.board.san(move)
        if self._candidate_count < self.stable_observations:
            self.state = TrackingState.CANDIDATE
            return TrackingUpdate(self.state, f"Candidate {san}; waiting for stable visual evidence ({self._candidate_count}/{self.stable_observations}).", move_uci, san, score, candidate_count=1, stable_count=self._candidate_count, candidate_summaries=(f"{move_uci}: visual {score:.3f}",))
        self.board.push(move); self.history.append(san)
        self._candidate_placement = None; self._candidate_count = 0; self.state = TrackingState.MOVE_CONFIRMED
        return TrackingUpdate(self.state, f"Confirmed {san} from visual change evidence.", move_uci, san, score, candidate_count=1, stable_count=self.stable_observations, candidate_summaries=(f"{move_uci}: visual {score:.3f}",))

    def observe_constrained_predictions(self, predictions: Mapping[str, Prediction], *, minimum_confidence: float = .55) -> TrackingUpdate:
        """Fallback: assess only squares a legal move would affect.

        This deliberately ignores misclassified *unrelated* pieces. It requires
        confident exact evidence on every affected square and still routes the
        result through the normal two-observation stability gate.
        """
        if self.board is None:
            return TrackingUpdate(TrackingState.WAITING_INITIAL, "Confirm an initial position before tracking.")
        candidates: list[chess.Move] = []
        for move in self.board.legal_moves:
            next_board = self.board.copy(stack=False); next_board.push(move)
            affected = _affected_squares_for_move(self.board, move)
            supported = True
            for square in affected:
                prediction = predictions.get(square)
                expected = next_board.piece_at(chess.parse_square(square))
                expected_state = _SYMBOL_TO_STATE[expected.symbol()] if expected else "empty"
                if prediction is None or prediction.uncertain or prediction.confidence < minimum_confidence or prediction.state != expected_state:
                    supported = False; break
            if supported:
                candidates.append(move)
        if len(candidates) != 1:
            self.state = TrackingState.UNCERTAIN
            reason = "no legal move has confident affected-square evidence" if not candidates else "multiple legal moves fit the affected-square evidence"
            return TrackingUpdate(self.state, "Recognition fallback could not safely identify a move; retained the tracked position.",
                                  candidate_count=len(candidates), rejection_reason=reason,
                                  candidate_summaries=tuple(move.uci() for move in candidates[:12]))
        return self.observe_visual_move(candidates[0].uci(), .70)

    def _recovery_candidates(self, observed: str) -> tuple[list[tuple[tuple[chess.Move, ...], int]], bool]:
        """Bounded breadth-first legal sequence search, scored by placement errors."""
        assert self.board is not None
        frontier = [(self.board.copy(stack=False), tuple())]
        matches: list[tuple[tuple[chess.Move, ...], int]] = []
        nodes, limited = 0, False
        for ply in range(1, self.max_recovery_plies + 1):
            next_frontier = []
            for board, sequence in frontier:
                for move in board.legal_moves:
                    nodes += 1
                    if nodes > self.max_recovery_nodes:
                        limited = True; break
                    child = board.copy(stack=False); child.push(move)
                    difference = placement_difference_count(observed, child.board_fen())
                    if difference <= self.max_piece_errors:
                        matches.append((sequence + (move,), difference))
                    if ply < self.max_recovery_plies:
                        next_frontier.append((child, sequence + (move,)))
                if limited: break
            if limited: break
            frontier = next_frontier
        return matches, limited

    def _pending_update(self, observed: str, authoritative: str, differences: int, confidence: float,
                        candidates: list[tuple[tuple[chess.Move, ...], int]], limited: bool) -> TrackingUpdate:
        best = min(score for _sequence, score in candidates)
        winners = [sequence for sequence, score in candidates if score == best]
        self._pending_sequences = winners
        self._pending_observed = observed
        self.state = TrackingState.RECOVERY_PENDING
        display = tuple(move.uci() for move in winners[0]) if winners else ()
        ambiguity = "multiple equally close legal sequences" if len(winners) > 1 else "a near match with recognition disagreement"
        if best == 0 and len(display) > 1:
            ambiguity = "a missed-move sequence"
        message = f"Tracking paused: {ambiguity}; confirm or resynchronize."
        if limited: message += " Search reached its safety limit."
        return TrackingUpdate(self.state, message, confidence=confidence, recognized_placement=observed,
                              authoritative_placement=authoritative, differing_squares=differences,
                              candidate_count=len(candidates), stable_count=self._candidate_count,
                              differing_square_names=placement_differences(observed, authoritative),
                              proposed_sequence=display, recovery_requires_confirmation=True,
                              rejection_reason=ambiguity,
                              candidate_summaries=tuple(f"{' '.join(move.uci() for move in sequence)}: {score} differing squares" for sequence, score in candidates[:20]))

    def confirm_proposed_sequence(self) -> TrackingUpdate:
        """Apply a single user-reviewed recovery sequence; never choose ambiguity."""
        if self.board is None or len(self._pending_sequences) != 1:
            return TrackingUpdate(self.state, "No single proposed sequence is available to confirm.")
        sequence = self._pending_sequences[0]
        san: list[str] = []
        for move in sequence:
            san.append(self.board.san(move)); self.board.push(move)
        self.history.extend(san); self._pending_sequences.clear(); self._pending_observed = None
        self.state = TrackingState.MOVE_CONFIRMED
        return TrackingUpdate(self.state, f"Confirmed recovery sequence: {' '.join(san)}.",
                              move_uci=sequence[-1].uci(), move_san=san[-1], proposed_sequence=tuple(move.uci() for move in sequence))

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
                                  differing_squares=differences, uncertain_recognition=True,
                                  differing_square_names=placement_differences(placement, authoritative), rejection_reason="uncertain changed recognition")
        candidates: list[chess.Move] = []
        for move in self.board.legal_moves:
            next_board = self.board.copy(stack=False); next_board.push(move)
            if next_board.board_fen() == placement:
                candidates.append(move)
        if len(candidates) != 1:
            recovery, limited = self._recovery_candidates(placement)
            if recovery:
                # Stable observations protect even user-confirmed recovery proposals.
                if placement == self._candidate_placement: self._candidate_count += 1
                else: self._candidate_placement, self._candidate_count = placement, 1
                if self._candidate_count >= self.stable_observations:
                    return self._pending_update(placement, authoritative, differences, confidence, recovery, limited)
                self.state = TrackingState.CANDIDATE
                return TrackingUpdate(self.state, "Recovery candidate detected; waiting for stable observation.", confidence=confidence,
                                      recognized_placement=placement, authoritative_placement=authoritative,
                                      differing_squares=differences, candidate_count=len(recovery), stable_count=self._candidate_count,
                                      differing_square_names=placement_differences(placement, authoritative))
            self.state = TrackingState.UNCERTAIN
            self._candidate_placement = None; self._candidate_count = 0
            reason = "does not match any legal move or bounded recovery sequence" if not candidates else "matches multiple legal moves"
            return TrackingUpdate(self.state, f"Recognized board {reason}; retained the tracked position.", confidence=confidence,
                                  recognized_placement=placement, authoritative_placement=authoritative,
                                  differing_squares=differences, candidate_count=len(candidates),
                                  differing_square_names=placement_differences(placement, authoritative), rejection_reason=reason)
        move = candidates[0]
        if placement == self._candidate_placement:
            self._candidate_count += 1
        else:
            self._candidate_placement, self._candidate_count = placement, 1
        san = self.board.san(move)
        if self._candidate_count < self.stable_observations:
            self.state = TrackingState.CANDIDATE
            return TrackingUpdate(self.state, f"Candidate {san}; waiting for stable observation ({self._candidate_count}/{self.stable_observations}).", move.uci(), san, confidence,
                                  placement, authoritative, differences, 1, self._candidate_count,
                                  candidate_summaries=(f"{move.uci()}: exact",))
        self.board.push(move); self.history.append(san)
        self._candidate_placement = None; self._candidate_count = 0
        self.state = TrackingState.MOVE_CONFIRMED
        return TrackingUpdate(self.state, f"Confirmed {san}.", move.uci(), san, confidence,
                              placement, authoritative, differences, 1, self.stable_observations,
                              candidate_summaries=(f"{move.uci()}: exact",))

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
