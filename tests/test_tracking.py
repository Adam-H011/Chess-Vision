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
    assert tracker.observe_placement(chess.STARTING_BOARD_FEN).state in {TrackingState.CANDIDATE, TrackingState.UNCERTAIN}
    assert tracker.history == ["e4"]


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


def test_two_missed_plies_proposes_unique_recovery_and_requires_confirmation():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    target = chess.Board(); target.push_uci("e2e4"); target.push_uci("c7c5")
    assert tracker.observe_placement(target.board_fen()).state is TrackingState.CANDIDATE
    update = tracker.observe_placement(target.board_fen())
    assert update.state is TrackingState.RECOVERY_PENDING
    assert update.proposed_sequence == ("e2e4", "c7c5")
    assert tracker.history == []
    confirmed = tracker.confirm_proposed_sequence()
    assert confirmed.state is TrackingState.MOVE_CONFIRMED
    assert tracker.board.board_fen() == target.board_fen()


def test_one_square_queen_knight_error_pauses_without_changing_authoritative_board():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    bad = chess.Board(); bad.remove_piece_at(chess.D8); bad.set_piece_at(chess.D8, chess.Piece(chess.KNIGHT, chess.BLACK))
    update = tracker.observe_placement(bad.board_fen())
    assert update.state in {TrackingState.CANDIDATE, TrackingState.UNCERTAIN, TrackingState.RECOVERY_PENDING}
    assert tracker.board.board_fen() == chess.STARTING_BOARD_FEN


def test_wrong_crop_empty_board_never_changes_authoritative_position():
    tracker = BoardTracker(); tracker.confirm_initial(chess.STARTING_FEN)
    update = tracker.observe_placement("8/8/8/8/8/8/8/8")
    assert update.state in {TrackingState.CANDIDATE, TrackingState.UNCERTAIN, TrackingState.RECOVERY_PENDING}
    assert tracker.board.board_fen() == chess.STARTING_BOARD_FEN


def test_black_nf6_is_confirmed_despite_unrelated_white_piece_errors():
    """Regression for real diagnostic: after d4, g8 is empty and f6 is a knight."""
    board = chess.Board(); board.push_uci("d2d4")
    tracker = BoardTracker(); tracker.confirm_initial(board.fen(en_passant="fen"))
    target = board.copy(stack=False); target.push_uci("g8f6")
    states = {"P": "white_pawn", "N": "white_knight", "B": "white_bishop", "R": "white_rook", "Q": "white_queen", "K": "white_king",
              "p": "black_pawn", "n": "black_knight", "b": "black_bishop", "r": "black_rook", "q": "black_queen", "k": "black_king"}
    predictions = {}
    for square in chess.SQUARES:
        piece = target.piece_at(square)
        state = states[piece.symbol()] if piece else "empty"
        # Simulate the reported unrelated white pawn→rook recognition noise.
        if piece and piece.color is chess.WHITE and piece.piece_type == chess.PAWN:
            state = "white_rook"
        predictions[chess.square_name(square)] = SimpleNamespace(state=state, confidence=.92, uncertain=False)
    assert predictions["g8"].state == "empty"
    assert predictions["f6"].state == "black_knight"
    assert tracker.observe_constrained_predictions(predictions).state is TrackingState.CANDIDATE
    update = tracker.observe_constrained_predictions(predictions)
    assert update.state is TrackingState.MOVE_CONFIRMED
    assert tracker.fen == target.fen(en_passant="fen")
    assert tracker.board.turn is chess.WHITE


def test_rejected_predictions_do_not_advance_authoritative_position():
    board = chess.Board(); board.push_uci("d2d4")
    tracker = BoardTracker(); tracker.confirm_initial(board.fen(en_passant="fen"))
    noisy = {"g8": SimpleNamespace(state="empty", confidence=.2, uncertain=True), "f6": SimpleNamespace(state="black_knight", confidence=.2, uncertain=True)}
    update = tracker.observe_constrained_predictions(noisy)
    assert update.state is TrackingState.UNCERTAIN
    assert tracker.fen == board.fen(en_passant="fen")


def test_black_d5_move_commit_updates_placement_and_turn_together():
    board = chess.Board(); board.push_uci("d2d4")
    tracker = BoardTracker(); tracker.confirm_initial(board.fen(en_passant="fen"))
    assert tracker.board.turn is chess.BLACK
    assert tracker.observe_visual_move("d7d5", .9).state is TrackingState.CANDIDATE
    assert tracker.observe_visual_move("d7d5", .9).state is TrackingState.MOVE_CONFIRMED
    assert tracker.board.piece_at(chess.D5) == chess.Piece(chess.PAWN, chess.BLACK)
    assert tracker.board.turn is chess.WHITE
    assert tracker.fen.split()[1] == "w"


def test_constrained_recognition_confirms_each_castle_from_king_and_rook_squares():
    states = {"P": "white_pawn", "N": "white_knight", "B": "white_bishop", "R": "white_rook", "Q": "white_queen", "K": "white_king",
              "p": "black_pawn", "n": "black_knight", "b": "black_bishop", "r": "black_rook", "q": "black_queen", "k": "black_king"}
    for turn, uci in (("w", "e1g1"), ("w", "e1c1"), ("b", "e8g8"), ("b", "e8c8")):
        before = chess.Board(f"r3k2r/8/8/8/8/8/8/R3K2R {turn} KQkq - 0 1")
        after = before.copy(stack=False); after.push_uci(uci)
        predictions = {
            chess.square_name(square): SimpleNamespace(state=states[piece.symbol()] if piece else "empty", confidence=.92, uncertain=False)
            for square in chess.SQUARES
            for piece in (after.piece_at(square),)
        }
        tracker = BoardTracker(); tracker.confirm_initial(before.fen(en_passant="fen"))
        assert tracker.observe_constrained_predictions(predictions).state is TrackingState.CANDIDATE
        assert tracker.observe_constrained_predictions(predictions).state is TrackingState.MOVE_CONFIRMED
        assert tracker.fen == after.fen(en_passant="fen")
