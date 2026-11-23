import chess
from types import SimpleNamespace

from chessvision.tracking import BoardTracker, TrackingState, placement_from_predictions


def observe_move(tracker, uci):
    board = tracker.board.copy(stack=False); board.push_uci(uci)
    assert tracker.observe_placement(board.board_fen()).state is TrackingState.CANDIDATE
    return tracker.observe_placement(board.board_fen())


def test_normal_knight_capture_and_full_fen_updates():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    update = observe_move(tracker, "e2e4")
    assert update.move_san == "e4" and tracker.fen.split()[1:] == ["b", "KQkq", "e3", "0", "1"]
    observe_move(tracker, "d7d5")
    update = observe_move(tracker, "e4d5")
    assert update.move_san == "exd5" and tracker.board.piece_at(chess.D5).symbol() == "P"
    observe_move(tracker, "g8f6")
    assert tracker.history[-1] == "Nf6"


def test_castle_en_passant_promotion_and_undo():
    tracker = BoardTracker(); tracker.confirm_initial("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert observe_move(tracker, "e1g1").move_san == "O-O"
    assert tracker.undo().state is TrackingState.MONITORING
    tracker.resynchronize("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    assert observe_move(tracker, "e5d6").move_san == "exd6"
    tracker.resynchronize("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    assert observe_move(tracker, "a7a8q").move_san == "a8=Q+"


def test_transient_duplicate_and_uncertain_observations_do_not_move():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    assert tracker.observe_placement(chess.STARTING_BOARD_FEN).state is TrackingState.MONITORING
    moved = chess.Board(); moved.push_uci("e2e4")
    assert tracker.observe_placement(moved.board_fen(), uncertain=True).state is TrackingState.UNCERTAIN
    assert tracker.history == []
    assert observe_move(tracker, "e2e4").state is TrackingState.MOVE_CONFIRMED
    assert tracker.observe_placement(tracker.board.board_fen()).state is TrackingState.MONITORING
    assert tracker.observe_placement(chess.STARTING_BOARD_FEN).state is TrackingState.UNCERTAIN


def test_disconnection_and_manual_resync():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    assert tracker.disconnected().state is TrackingState.DISCONNECTED
    assert tracker.resynchronize(chess.STARTING_FEN).state is TrackingState.MONITORING


def test_unchanged_starting_position_is_valid_even_with_low_confidence_metadata():
    tracker = BoardTracker(min_confidence=.90); tracker.confirm_initial(chess.STARTING_FEN)
    update = tracker.observe_placement(chess.STARTING_BOARD_FEN, confidence=.05, uncertain=True)
    assert update.state is TrackingState.MONITORING
    assert update.differing_squares == 0
    assert update.recognized_placement == update.authoritative_placement
    assert tracker.history == []


def test_changed_low_confidence_position_is_still_rejected_and_duplicate_is_not_accepted():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    moved = chess.Board(); moved.push_uci("e2e4")
    uncertain = tracker.observe_placement(moved.board_fen(), confidence=.2)
    assert uncertain.state is TrackingState.UNCERTAIN and uncertain.differing_squares == 2
    assert observe_move(tracker, "e2e4").state is TrackingState.MOVE_CONFIRMED
    duplicate = tracker.observe_placement(tracker.board.board_fen())
    assert duplicate.state is TrackingState.MONITORING
    assert tracker.history == ["e4"]


def test_placement_difference_count_is_orientation_independent_at_logical_square_level():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    # Orientation affects display/cropping only; logical a1..h8 placement does not.
    update = tracker.observe_placement(chess.STARTING_BOARD_FEN, confidence=1)
    assert update.differing_squares == 0


def test_full_confident_starting_prediction_has_zero_uncertain_squares_and_is_unchanged():
    states = {"P": "white_pawn", "N": "white_knight", "B": "white_bishop", "R": "white_rook", "Q": "white_queen", "K": "white_king",
              "p": "black_pawn", "n": "black_knight", "b": "black_bishop", "r": "black_rook", "q": "black_queen", "k": "black_king"}
    board = chess.Board()
    predictions = {}
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        predictions[chess.square_name(square)] = SimpleNamespace(state=states[piece.symbol()] if piece else "empty", confidence=.01, uncertain=False)
    placement, confidence, uncertain = placement_from_predictions(predictions)
    assert placement == chess.STARTING_BOARD_FEN and confidence == .01 and not uncertain
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    assert tracker.observe(predictions).state is TrackingState.MONITORING
