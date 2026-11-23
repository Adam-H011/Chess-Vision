import chess
import pytest

from chessvision.position import AnalysisMode, Position, PositionMetadata, Source


def test_fen_and_pgn_import_preserve_state():
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    position = Position.from_fen(fen)
    assert position.fen() == fen
    assert position.validate()[0]
    assert Position.from_pgn("1. e4 e5 2. Nf3 Nc6").fen() == fen


def test_incomplete_position_never_becomes_fen():
    position = Position(chess.STARTING_BOARD_FEN, PositionMetadata(turn="w"), source=Source.BROWSER)
    assert position.fen() is None
    assert not position.validate()[0]


def test_browser_payload_keeps_unknown_metadata_and_rejects_malformed():
    position = Position.from_browser_payload({"placement": chess.STARTING_BOARD_FEN, "metadata": {}, "orientation": "black"})
    assert position.orientation == "black"
    assert position.fen() is None
    with pytest.raises(ValueError):
        Position.from_browser_payload({"placement": 4})


def test_live_mode_disallows_engine():
    assert not AnalysisMode.LIVE_COMPETITIVE.engine_permitted
    assert not AnalysisMode.UNKNOWN.engine_permitted
    assert AnalysisMode.TRAINING.engine_permitted
