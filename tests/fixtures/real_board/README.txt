ChessVision real-board regression fixtures

board_crop.png: user-supplied chessboard crop, standard initial position, White at bottom.
grid_overlay.png: board with detected grid lines.
recognition.json: original erroneous recognition, 64/64 empty.

Expected piece placement FEN: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR
Expected complete FEN for a new game: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1

These files should be extracted into tests/fixtures/real_board/ inside the repository.
Note: this is ONE real starting-position fixture. It cannot prove generalization to other themes or positions.
