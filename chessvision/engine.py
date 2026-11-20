"""Local Stockfish analysis. Numeric evaluations are from White's perspective."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Optional

import chess
import chess.engine


class StockfishNotFoundError(RuntimeError):
    """No usable Stockfish executable could be located."""


class InvalidPositionError(ValueError):
    """A FEN is syntactically valid but its position is not."""


class EngineAnalysisError(RuntimeError):
    """Stockfish could not start or analyze the requested position."""


@dataclass(frozen=True)
class AnalysisResult:
    """One engine principal variation.

    Centipawns are from White's perspective. They are ``None`` for a mate;
    ``mate_distance`` then holds the signed White-perspective mate distance.
    """

    move_san: str
    move_uci: str
    evaluation_centipawns: Optional[int]
    mate_distance: Optional[int]
    principal_variation_san: list[str]
    depth: Optional[int]


@dataclass(frozen=True)
class AnalysisReport:
    """Position analysis plus terminal-state information."""

    variations: list[AnalysisResult]
    terminal: bool = False
    result: Optional[str] = None
    termination: Optional[str] = None


class ChessAnalyzer:
    """Manage a local Stockfish process; usable as a context manager."""

    def __init__(self, stockfish_path: str | None = None) -> None:
        self._requested_path = stockfish_path
        self._runner: asyncio.Runner | None = None
        self._protocol: chess.engine.UciProtocol | None = None
        self._transport: asyncio.SubprocessTransport | None = None
        self._executable: str | None = None

    @property
    def is_running(self) -> bool:
        return self._protocol is not None

    def _find_executable(self) -> str:
        if self._requested_path:
            requested = Path(self._requested_path).expanduser()
            if requested.is_file():
                return str(requested)
            discovered = shutil.which(self._requested_path)
            if discovered:
                return discovered
            raise StockfishNotFoundError(
                f"Stockfish was not found at '{self._requested_path}'. Pass a valid "
                "executable path or install Stockfish."
            )
        discovered = shutil.which("stockfish")
        if discovered:
            return discovered
        raise StockfishNotFoundError(
            "Stockfish executable not found on PATH. On Fedora install it with "
            "'sudo dnf install stockfish', or pass --stockfish /path/to/stockfish."
        )

    def start(self) -> None:
        """Start Stockfish once, locating it first if necessary."""
        if self._protocol is not None:
            return
        self._executable = self._find_executable()
        runner = asyncio.Runner()
        try:
            transport, protocol = runner.run(self._open_protocol(self._executable))
        except (OSError, asyncio.TimeoutError, chess.engine.EngineError) as exc:
            runner.close()
            raise EngineAnalysisError(
                f"Could not start Stockfish at '{self._executable}': {exc}"
            ) from exc
        self._runner = runner
        self._transport = transport
        self._protocol = protocol

    @staticmethod
    async def _open_protocol(
        executable: str,
    ) -> tuple[asyncio.SubprocessTransport, chess.engine.UciProtocol]:
        """Open and initialize python-chess's async UCI protocol."""
        transport, protocol = await chess.engine.UciProtocol.popen(executable)
        await protocol.initialize()
        return transport, protocol

    def close(self) -> None:
        """Stop the child process; safe to call repeatedly."""
        runner, protocol, transport = self._runner, self._protocol, self._transport
        self._runner = None
        self._protocol = None
        self._transport = None
        if runner is not None:
            try:
                if protocol is not None:
                    runner.run(protocol.quit())
            except (asyncio.TimeoutError, chess.engine.EngineError, OSError):
                pass
            finally:
                if transport is not None:
                    transport.close()
                runner.close()

    def __enter__(self) -> ChessAnalyzer:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def board_from_fen(fen: str) -> chess.Board:
        """Parse FEN and ensure it represents a valid chess position."""
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise ValueError(f"Invalid FEN: {exc}") from exc
        if not board.is_valid():
            raise InvalidPositionError(
                f"Invalid chess position (status: {board.status()})."
            )
        return board

    @staticmethod
    def _pv_san(board: chess.Board, moves: list[chess.Move]) -> list[str]:
        variation = board.copy(stack=False)
        result: list[str] = []
        for move in moves:
            if move not in variation.legal_moves:
                break
            result.append(variation.san(move))
            variation.push(move)
        return result

    @classmethod
    def _make_result(cls, board: chess.Board, info: dict[str, Any]) -> AnalysisResult | None:
        pv = list(info.get("pv", []))
        if not pv or pv[0] not in board.legal_moves:
            return None
        centipawns: int | None = None
        mate_distance: int | None = None
        score = info.get("score")
        if score is not None:
            white_score = score.white()
            if white_score.is_mate():
                mate_distance = white_score.mate()
            else:
                centipawns = white_score.score()
        return AnalysisResult(
            move_san=board.san(pv[0]),
            move_uci=pv[0].uci(),
            evaluation_centipawns=centipawns,
            mate_distance=mate_distance,
            principal_variation_san=cls._pv_san(board, pv),
            depth=info.get("depth"),
        )

    def analyze(self, fen: str, depth: int = 18, multipv: int = 3) -> AnalysisReport:
        """Return best lines for ``fen``; evaluations always favor White when positive."""
        if depth < 1:
            raise ValueError("depth must be at least 1")
        if multipv < 1:
            raise ValueError("multipv must be at least 1")
        board = self.board_from_fen(fen)
        outcome = board.outcome(claim_draw=True)
        if outcome is not None:
            return AnalysisReport(
                variations=[], terminal=True, result=outcome.result(),
                termination=outcome.termination.name.replace("_", " ").title(),
            )
        self.start()
        assert self._runner is not None
        assert self._protocol is not None
        try:
            infos = self._runner.run(
                self._protocol.analyse(
                    board, chess.engine.Limit(depth=depth), multipv=multipv
                )
            )
        except (asyncio.TimeoutError, chess.engine.EngineError, OSError) as exc:
            raise EngineAnalysisError(f"Stockfish analysis failed: {exc}") from exc
        if isinstance(infos, dict):
            infos = [infos]
        parsed = [self._make_result(board, info) for info in infos]
        return AnalysisReport([result for result in parsed if result is not None])
