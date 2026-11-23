"""Source-neutral chess positions with explicit metadata completeness.

This module deliberately does not fill in state that a source did not provide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import chess
import chess.pgn


class Source(str, Enum):
    BROWSER = "browser"
    SCREENSHOT = "screenshot"
    FEN_PGN = "fen-pgn"
    MANUAL = "manual"


class AnalysisMode(str, Enum):
    OFFLINE = "offline"
    COMPLETED = "completed-game"
    TRAINING = "permitted-training"
    LIVE_COMPETITIVE = "live-competitive"
    UNKNOWN = "unknown"

    @property
    def engine_permitted(self) -> bool:
        return self in {self.OFFLINE, self.COMPLETED, self.TRAINING}


@dataclass(frozen=True)
class PositionMetadata:
    turn: str | None = None
    castling: str | None = None
    ep_square: str | None = None
    halfmove_clock: int | None = None
    fullmove_number: int | None = None

    @property
    def complete(self) -> bool:
        return all(value is not None and value != "" for value in (
            self.turn, self.castling, self.ep_square, self.halfmove_clock, self.fullmove_number))


@dataclass(frozen=True)
class Position:
    """A placement plus only the game state actually supplied by its source."""
    placement: str
    metadata: PositionMetadata = PositionMetadata()
    orientation: str = "white"
    source: Source = Source.MANUAL
    confidence: float | None = None
    complete: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)
    mode: AnalysisMode = AnalysisMode.UNKNOWN

    def fen(self) -> str | None:
        if not self.metadata.complete:
            return None
        m = self.metadata
        return f"{self.placement} {m.turn} {m.castling} {m.ep_square} {m.halfmove_clock} {m.fullmove_number}"

    def validate(self) -> tuple[bool, tuple[str, ...]]:
        warnings = list(self.warnings)
        fen = self.fen()
        if fen is None:
            warnings.append("Position metadata is incomplete; no authoritative FEN is available.")
            return False, tuple(warnings)
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            warnings.append(f"Invalid FEN: {exc}")
            return False, tuple(warnings)
        if not board.is_valid():
            warnings.append(f"Illegal or implausible position (python-chess status {board.status()}).")
            return False, tuple(warnings)
        return True, tuple(warnings)

    @classmethod
    def from_fen(cls, fen: str, *, source: Source = Source.FEN_PGN, orientation: str = "white", mode: AnalysisMode = AnalysisMode.OFFLINE) -> "Position":
        board = chess.Board(fen)
        parts = board.fen().split()
        return cls(parts[0], PositionMetadata(parts[1], parts[2], parts[3], int(parts[4]), int(parts[5])), orientation, source, 1.0, True, (), mode)

    @classmethod
    def from_pgn(cls, pgn_text: str, *, orientation: str = "white", mode: AnalysisMode = AnalysisMode.OFFLINE) -> "Position":
        game = chess.pgn.read_game(__import__("io").StringIO(pgn_text))
        if game is None:
            raise ValueError("No readable PGN game found.")
        board = game.end().board()
        return cls.from_fen(board.fen(), source=Source.FEN_PGN, orientation=orientation, mode=mode)

    @classmethod
    def from_browser_payload(cls, payload: dict[str, Any]) -> "Position":
        placement = payload.get("placement")
        if not isinstance(placement, str) or len(placement) > 100:
            raise ValueError("Browser payload has no valid placement.")
        raw = payload.get("metadata") or {}
        if not isinstance(raw, dict):
            raise ValueError("Browser metadata must be an object.")
        def optional_str(name: str) -> str | None:
            value = raw.get(name)
            return value if isinstance(value, str) and len(value) <= 10 else None
        def optional_int(name: str) -> int | None:
            value = raw.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        orientation = payload.get("orientation", "white")
        if orientation not in ("white", "black"):
            orientation = "white"
        requested_mode = payload.get("mode", AnalysisMode.UNKNOWN.value)
        # Browser supplied labels are advisory only: never elevate unknown/live to safe.
        mode = AnalysisMode(requested_mode) if requested_mode in {x.value for x in AnalysisMode} else AnalysisMode.UNKNOWN
        if mode not in (AnalysisMode.OFFLINE, AnalysisMode.COMPLETED, AnalysisMode.TRAINING):
            mode = AnalysisMode.UNKNOWN if mode is AnalysisMode.UNKNOWN else mode
        warnings = tuple(str(w)[:300] for w in payload.get("warnings", []) if isinstance(w, str))[:20]
        confidence = payload.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) and 0 <= confidence <= 1 else None
        position = cls(placement, PositionMetadata(optional_str("turn"), optional_str("castling"), optional_str("ep"), optional_int("halfmove"), optional_int("fullmove")), orientation, Source.BROWSER, confidence, False, warnings, mode)
        valid, warnings = position.validate()
        return cls(**{**position.__dict__, "complete": valid, "warnings": warnings})
