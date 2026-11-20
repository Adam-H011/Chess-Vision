"""Command-line demonstration for the ChessVision Stockfish engine."""

from __future__ import annotations

import argparse

import chess

from chessvision.engine import (
    ChessAnalyzer,
    EngineAnalysisError,
    InvalidPositionError,
    StockfishNotFoundError,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a position locally with Stockfish.")
    parser.add_argument("--fen", default=chess.STARTING_FEN, help="Position in FEN format.")
    parser.add_argument("--depth", type=int, default=18, help="Search depth (default: 18).")
    parser.add_argument("--multipv", type=int, default=3, help="Best lines to show (default: 3).")
    parser.add_argument("--stockfish", help="Path to Stockfish (defaults to PATH).")
    return parser.parse_args()


def format_evaluation(centipawns: int | None, mate_distance: int | None) -> str:
    if mate_distance is not None:
        return f"Mate in {mate_distance}" if mate_distance > 0 else f"Mated in {-mate_distance}"
    return "Unknown" if centipawns is None else f"{centipawns / 100:+.2f}"


def main() -> int:
    args = parse_args()
    print("ChessVision – Stockfish Analysis")
    try:
        board = ChessAnalyzer.board_from_fen(args.fen)
        print("\nPosition:")
        print(board)
        print("\nStarting Stockfish…")
        with ChessAnalyzer(args.stockfish) as analyzer:
            report = analyzer.analyze(args.fen, args.depth, args.multipv)
    except (ValueError, InvalidPositionError, StockfishNotFoundError, EngineAnalysisError) as exc:
        print(f"Error: {exc}")
        return 1
    if report.terminal:
        print(f"\nGame over: {report.termination} ({report.result})")
        return 0
    print("\nBest moves (evaluation is from White's perspective):")
    for number, line in enumerate(report.variations, 1):
        depth = f", depth {line.depth}" if line.depth is not None else ""
        print(f"{number}. {line.move_san} ({line.move_uci}) — "
              f"{format_evaluation(line.evaluation_centipawns, line.mate_distance)}{depth}")
        print(f"   PV: {' '.join(line.principal_variation_san)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
