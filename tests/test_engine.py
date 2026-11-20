from __future__ import annotations

from unittest.mock import Mock

import chess
import chess.engine
import pytest

from chessvision.engine import ChessAnalyzer, InvalidPositionError, StockfishNotFoundError


class FakeEngine:
    def __init__(self, infos: list[dict]) -> None:
        self.infos = infos
        self.quit_called = False

    async def initialise(self) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def analyse(self, board: chess.Board, limit: chess.engine.Limit, multipv: int):
        return self.infos[:multipv]

    async def quit(self) -> None:
        self.quit_called = True


class FakeTransport:
    def __init__(self) -> None:
        self.close_called = False

    def close(self) -> None:
        self.close_called = True


def info_for(board: chess.Board, move: chess.Move, score: chess.engine.PovScore) -> dict:
    next_board = board.copy()
    next_board.push(move)
    return {"pv": [move, next(iter(next_board.legal_moves))], "score": score, "depth": 12}


def mock_engine(monkeypatch: pytest.MonkeyPatch, engine: FakeEngine) -> None:
    monkeypatch.setattr("chessvision.engine.shutil.which", lambda _: "/usr/bin/stockfish")
    transport = FakeTransport()

    async def popen(_: str) -> tuple[FakeTransport, FakeEngine]:
        return transport, engine

    monkeypatch.setattr(chess.engine.UciProtocol, "popen", popen)


def test_valid_starting_position_fen() -> None:
    assert ChessAnalyzer.board_from_fen(chess.STARTING_FEN).fen() == chess.STARTING_FEN


def test_invalid_fen_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid FEN"):
        ChessAnalyzer.board_from_fen("not a FEN")


def test_invalid_chess_position_is_rejected() -> None:
    with pytest.raises(InvalidPositionError):
        ChessAnalyzer.board_from_fen("8/8/8/8/8/8/8/4K3 w - - 0 1")


def test_missing_stockfish_is_helpful(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chessvision.engine.shutil.which", lambda _: None)
    with pytest.raises(StockfishNotFoundError, match="dnf install stockfish"):
        ChessAnalyzer().start()


def test_legal_moves_and_san_uci_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    board, move = chess.Board(), chess.Move.from_uci("e2e4")
    engine = FakeEngine([info_for(board, move, chess.engine.PovScore(chess.engine.Cp(34), chess.WHITE))])
    mock_engine(monkeypatch, engine)
    with ChessAnalyzer() as analyzer:
        report = analyzer.analyze(board.fen())
    assert move in board.legal_moves
    assert report.variations[0].move_san == "e4"
    assert report.variations[0].move_uci == "e2e4"


def test_mate_score_is_not_centipawns(monkeypatch: pytest.MonkeyPatch) -> None:
    board, move = chess.Board(), chess.Move.from_uci("e2e4")
    engine = FakeEngine([info_for(board, move, chess.engine.PovScore(chess.engine.Mate(3), chess.WHITE))])
    mock_engine(monkeypatch, engine)
    with ChessAnalyzer() as analyzer:
        result = analyzer.analyze(board.fen()).variations[0]
    assert result.evaluation_centipawns is None
    assert result.mate_distance == 3


def test_terminal_position_does_not_start_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    start = Mock()
    monkeypatch.setattr(ChessAnalyzer, "start", start)
    report = ChessAnalyzer().analyze("7k/5Q2/7K/8/8/8/8/8 b - - 0 1")
    assert report.terminal and report.variations == []
    start.assert_not_called()


def test_context_manager_closes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine([])
    mock_engine(monkeypatch, engine)
    with ChessAnalyzer() as analyzer:
        assert analyzer.is_running
    assert engine.quit_called and not analyzer.is_running


def test_multiple_principal_variations(monkeypatch: pytest.MonkeyPatch) -> None:
    board = chess.Board()
    moves = [chess.Move.from_uci(value) for value in ("e2e4", "d2d4", "g1f3")]
    infos = [info_for(board, move, chess.engine.PovScore(chess.engine.Cp(score), chess.WHITE))
             for move, score in zip(moves, (30, 20, 10))]
    mock_engine(monkeypatch, FakeEngine(infos))
    with ChessAnalyzer() as analyzer:
        report = analyzer.analyze(board.fen(), multipv=3)
    assert [line.move_uci for line in report.variations] == [move.uci() for move in moves]
    assert [line.evaluation_centipawns for line in report.variations] == [30, 20, 10]
