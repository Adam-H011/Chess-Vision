from __future__ import annotations

import pytest

from chessvision.fen_builder import FenBuildError, FenMetadata, build_fen, validate_fen
from chessvision.piece_recognition import PiecePrediction


METADATA = FenMetadata("w", "KQkq", "-", 0, 1)


def test_starting_position_fen_and_validation() -> None:
    pieces = {}
    for file_name in "abcdefgh":
        pieces[f"{file_name}2"] = "white_pawn"
        pieces[f"{file_name}7"] = "black_pawn"
    pieces.update({"a1": "white_rook", "h1": "white_rook", "e1": "white_king", "e8": "black_king", "a8": "black_rook", "h8": "black_rook"})
    fen = build_fen(pieces, METADATA)
    assert fen.startswith("r3k2r/pppppppp/8/")
    assert validate_fen(fen).position_valid


def test_metadata_and_uncertainty_require_confirmation() -> None:
    with pytest.raises(FenBuildError):
        build_fen({}, FenMetadata())
    with pytest.raises(FenBuildError, match="uncertain"):
        build_fen({"e1": PiecePrediction("white_king", 0.2, True)}, METADATA)


def test_syntax_and_position_validation_are_distinct() -> None:
    assert not validate_fen("nope").syntactically_valid
    invalid_position = validate_fen("8/8/8/8/8/8/8/4K3 w - - 0 1")
    assert invalid_position.syntactically_valid and not invalid_position.position_valid
    assert "black king is missing" in invalid_position.message
