/* Generic, defensive adapters. Chess.com DOM compatibility is intentionally unverified. */
const api=globalThis.browser||globalThis.chrome, FILES='abcdefgh';
function fenPlacementFromText(text) { const match=(text||'').match(/(?:^|\s)([prnbqkPRNBQK1-8]+(?:\/[prnbqkPRNBQK1-8]+){7})(?:\s|$)/); return match&&match[1]; }
function explicitData() {
  const text=document.body.innerText||''; const placement=fenPlacementFromText(text);
  if (!placement) return null;
  const full=text.match(/([prnbqkPRNBQK1-8]+(?:\/[prnbqkPRNBQK1-8]+){7}\s+[wb]\s+(?:K?Q?k?q?|- )?)/);
  return {placement,metadata:{},orientation:'white',confidence:1,warnings:['FEN-like placement found in page text; game metadata was not reliably exposed.']};
}
function domPieces() {
  const candidates=[...document.querySelectorAll('[data-square][data-piece], [data-square][class*="piece"], .piece')];
  const board={}; let ambiguous=false;
  for (const el of candidates) {
    const coords=[...el.classList].map(x=>x.match(/^square-([1-8])([1-8])$/)).find(Boolean);
    const square=el.dataset.square || (coords ? FILES[+coords[1]-1]+coords[2] : null);
    const raw=el.dataset.piece||[...el.classList].join(' ');
    const m=raw.match(/(?:white|w)[-_]?(pawn|knight|bishop|rook|queen|king)|(?:black|b)[-_]?(pawn|knight|bishop|rook|queen|king)/i);
    if (!square || !/^[a-h][1-8]$/.test(square) || !m) { ambiguous=true; continue; }
    const white=/white|\bw[-_]/i.test(m[0]); const letter={pawn:'p',knight:'n',bishop:'b',rook:'r',queen:'q',king:'k'}[m[1].toLowerCase()];
    if (board[square]) ambiguous=true; board[square]=white?letter.toUpperCase():letter;
  }
  if (!Object.keys(board).length) return null;
  let ranks=[]; for(let rank=8;rank;rank--){let run=0,row='';for(const file of FILES){const p=board[file+rank];if(p){if(run)row+=run,run=0;row+=p}else run++}ranks.push(row+(run||''));}
  return {placement:ranks.join('/'),metadata:{},orientation:'white',confidence:ambiguous?.45:.7,warnings:[...(ambiguous?['Some DOM pieces were ambiguous.']:[]),'Piece placement only; turn, castling, en passant, and clocks are unknown.']};
}
function extract() { return explicitData() || domPieces() || {error:'No supported structured position or unambiguous board-piece data found. Chess.com layout compatibility is unverified.'}; }
api.runtime.onMessage.addListener(message => message.type==='extract' ? extract() : undefined);
