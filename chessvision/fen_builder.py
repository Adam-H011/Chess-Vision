"""Build explicitly confirmed, metadata-complete FEN values from recognition."""

from __future__ import annotations

from dataclasses import dataclass

import chess

from .piece_recognition import PIECE_TO_FEN, PiecePrediction


class FenBuildError(ValueError):
    """Recognition or metadata cannot safely form a FEN."""


@dataclass(frozen=True)
class FenMetadata:
    side_to_move: str | None = None
    castling_rights: str | None = None
    en_passant: str | None = None
    halfmove_clock: int | None = None
    fullmove_number: int | None = None

    def fields(self) -> tuple[str, str, str, int, int]:
        values = (self.side_to_move, self.castling_rights, self.en_passant, self.halfmove_clock, self.fullmove_number)
        if any(value is None or value == "" for value in values):
            raise FenBuildError("Set side to move, castling, en passant, halfmove, and fullmove metadata.")
        if self.side_to_move not in ("w", "b"):
            raise FenBuildError("Side to move must be 'w' or 'b'.")
        return values  # type: ignore[return-value]


@dataclass(frozen=True)
class FenValidation:
    fen: str
    syntactically_valid: bool
    position_valid: bool
    message: str


_STATUS_MESSAGES = (
    (chess.STATUS_NO_WHITE_KING, "white king is missing"),
    (chess.STATUS_NO_BLACK_KING, "black king is missing"),
    (chess.STATUS_TOO_MANY_KINGS, "more than one king was detected"),
    (chess.STATUS_PAWNS_ON_BACKRANK, "a pawn is on the first or eighth rank"),
    (chess.STATUS_TOO_MANY_WHITE_PAWNS, "more than eight white pawns were detected"),
    (chess.STATUS_TOO_MANY_BLACK_PAWNS, "more than eight black pawns were detected"),
    (chess.STATUS_TOO_MANY_WHITE_PIECES, "more than sixteen white pieces were detected"),
    (chess.STATUS_TOO_MANY_BLACK_PIECES, "more than sixteen black pieces were detected"),
)


def position_issues(board: chess.Board) -> tuple[str, ...]:
    """Return human-readable recognition warnings without changing pieces."""
    status = board.status()
    issues = [message for flag, message in _STATUS_MESSAGES if status & flag]
    return tuple(issues or [f"python-chess status {status}"])


def build_fen(predictions: dict[str, PiecePrediction | str], metadata: FenMetadata, *, allow_uncertain: bool = False) -> str:
    side, castling, en_passant, halfmove, fullmove = metadata.fields()
    ranks: list[str] = []
    for rank in range(8, 0, -1):
        empty, cells = 0, []
        for file_name in "abcdefgh":
            item = predictions.get(f"{file_name}{rank}", "empty")
            prediction = item if isinstance(item, PiecePrediction) else PiecePrediction(item, 1.0, False)
            if prediction.uncertain and not allow_uncertain:
                raise FenBuildError(f"Recognition is uncertain at {file_name}{rank}.")
            if prediction.state == "empty":
                empty += 1
            else:
                if prediction.state not in PIECE_TO_FEN:
                    raise FenBuildError(f"Unknown piece state at {file_name}{rank}: {prediction.state}.")
                if empty:
                    cells.append(str(empty)); empty = 0
                cells.append(PIECE_TO_FEN[prediction.state])
        if empty:
            cells.append(str(empty))
        ranks.append("".join(cells))
    return f"{'/'.join(ranks)} {side} {castling} {en_passant} {halfmove} {fullmove}"


def validate_fen(fen: str) -> FenValidation:
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        return FenValidation(fen, False, False, f"Invalid FEN syntax: {exc}")
    if not board.is_valid():
        issues = "; ".join(position_issues(board))
        return FenValidation(fen, True, False, f"Suspicious recognized position: {issues}. Review the highlighted board editor squares.")
    return FenValidation(fen, True, True, "Valid FEN and plausible chess position.")
