/* Konvertierung zwischen Bild-Raster, 64er-Brett und FEN. a1 = Index 0. */

export const TEMPLATE_TO_SYMBOL = {
  wp: 'P', wn: 'N', wb: 'B', wr: 'R', wq: 'Q', wk: 'K',
  bp: 'p', bn: 'n', bb: 'b', br: 'r', bq: 'q', bk: 'k'
};

export const SYMBOLS = Object.keys(TEMPLATE_TO_SYMBOL).map(key => TEMPLATE_TO_SYMBOL[key]);

export const EMPTY = Array(64).fill(null);

/** grid[r][c] ist von oben links gelesen; orientation bestimmt die Seite. */
export function gridToBoard(grid, orientation) {
  const board = Array(64).fill(null);
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const square = squareFromScreen(r, c, orientation);
      board[square] = grid[r][c] || null;
    }
  }
  return board;
}

export function squareFromScreen(r, c, orientation) {
  const file = orientation === 'black' ? 7 - c : c;
  const rank = orientation === 'black' ? r : 7 - r;
  return rank * 8 + file;
}

export function screenFromSquare(square, orientation) {
  const file = square % 8;
  const rank = Math.floor(square / 8);
  return orientation === 'black'
    ? { r: rank, c: 7 - file }
    : { r: 7 - rank, c: file };
}

export function rotateBoard(board) {
  const out = Array(64).fill(null);
  for (let sq = 0; sq < 64; sq++) {
    const file = sq % 8;
    const rank = Math.floor(sq / 8);
    out[(7 - rank) * 8 + (7 - file)] = board[sq];
  }
  return out;
}

export function makeFen(board, turn = 'w') {
  let placement = '';
  for (let rank = 7; rank >= 0; rank--) {
    let empty = 0;
    for (let file = 0; file < 8; file++) {
      const piece = board[rank * 8 + file];
      if (!piece) {
        empty++;
      } else {
        if (empty) { placement += empty; empty = 0; }
        placement += piece;
      }
    }
    if (empty) placement += empty;
    if (rank > 0) placement += '/';
  }
  let castling = '';
  if (board[4] === 'K' && board[7] === 'R') castling += 'K';
  if (board[4] === 'K' && board[0] === 'R') castling += 'Q';
  if (board[60] === 'k' && board[63] === 'r') castling += 'k';
  if (board[60] === 'k' && board[56] === 'r') castling += 'q';
  return `${placement} ${turn} ${castling || '-'} - 0 1`;
}
