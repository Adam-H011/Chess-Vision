/*
 * ChessVison content script: markiert das Brett und zeichnet den Stockfish-Zug
 * als Overlay direkt auf die markierte Fläche. Läuft in allen Tabs.
 */
(() => {
  'use strict';

  const FILES = 'abcdefgh';
  const GLYPHS = {
    K: '♔', Q: '♕', R: '♖', B: '♗', N: '♘', P: '♙',
    k: '♚', q: '♛', r: '♜', b: '♝', n: '♞', p: '♟'
  };
  const PIECES = ['K', 'Q', 'R', 'B', 'N', 'P', 'k', 'q', 'r', 'b', 'n', 'p'];

  const CSS = `
    .cv-root { position: fixed; inset: 0; z-index: 2147483000; pointer-events: none;
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #eafaf1; }
    .cv-veil { position: absolute; inset: 0; background: rgba(4, 12, 10, .62);
      pointer-events: auto; cursor: crosshair; }
    .cv-help { position: absolute; left: 50%; top: 16px; transform: translateX(-50%);
      background: #10241d; border: 1px solid #2ee6a0; border-radius: 10px;
      padding: 8px 14px; font-size: 13px; line-height: 1.4; text-align: center;
      box-shadow: 0 12px 40px rgba(0,0,0,.5); pointer-events: none; max-width: min(560px, 90vw); }
    .cv-help b { color: #2ee6a0; }
    .cv-box { position: absolute; display: none; border: 3px dashed #2ee6a0;
      background: rgba(46, 230, 160, .12); box-shadow: 0 0 0 9999px rgba(4,12,10,.35); }
    .cv-size { position: absolute; top: -26px; left: 0; background: #10241d; color: #2ee6a0;
      padding: 2px 8px; border-radius: 6px; font-size: 12px; white-space: nowrap; }
    .cv-frame { position: fixed; pointer-events: none; }
    .cv-grid, .cv-svg { position: absolute; inset: 0; width: 100%; height: 100%; }
    .cv-svg { overflow: visible; filter: drop-shadow(0 2px 3px rgba(0,0,0,.65)); }
    .cv-cell { position: absolute; border: 1px dashed rgba(255,255,255,.35);
      background: rgba(0, 200, 130, .04); pointer-events: auto; cursor: pointer; }
    .cv-cell:hover { background: rgba(46, 230, 160, .35); }
    .cv-cell.changed { background: rgba(255, 193, 7, .4); }
    .cv-cell.cv-white { box-shadow: inset 0 0 0 3px rgba(255,255,255,.9); }
    .cv-cell.cv-black { box-shadow: inset 0 0 0 3px rgba(0,0,0,.9); }
    .cv-cell span { position: absolute; inset: 0; display: flex; align-items: center;
      justify-content: center; font-size: clamp(14px, 4vw, 34px); line-height: 1; }
    .cv-cell.cv-white span { color: #fff; text-shadow: 0 0 3px #000, 0 0 6px #000; }
    .cv-cell.cv-black span { color: #0b0b0b; text-shadow: 0 0 3px #fff, 0 0 6px #fff; }
    .cv-badge { position: absolute; left: 0; top: -46px; display: flex; align-items: center;
      gap: 8px; background: #10241d; border: 1px solid #2ee6a0; border-radius: 10px;
      padding: 6px 10px; font-size: 13px; white-space: nowrap; pointer-events: auto;
      box-shadow: 0 6px 24px rgba(0,0,0,.45); }
    .cv-badge .cv-move { color: #2ee6a0; font-weight: 800; font-size: 16px; }
    .cv-badge .cv-eval { color: #cfe9dd; }
    .cv-badge .cv-side { color: #ffd54f; font-size: 11px; font-weight: 700; }
    .cv-badge .cv-live { color: #7fcdb2; font-size: 11px; letter-spacing: .04em; }
    .cv-badge .cv-live.on { color: #2ee6a0; }
    .cv-badge button { background: #1c3a30; color: #dff7ec; border: 1px solid #35705c;
      border-radius: 6px; padding: 2px 7px; cursor: pointer; font-size: 13px; line-height: 1.4; }
    .cv-badge button:hover { background: #2ee6a0; color: #072015; border-color: #2ee6a0; }
    .cv-badge button.live-on { background: #2ee6a0; color: #072015; border-color: #2ee6a0; }
    .cv-spinner { display: inline-block; width: 10px; height: 10px; border: 2px solid #2ee6a0;
      border-right-color: transparent; border-radius: 50%; animation: cvspin .7s linear infinite; }
    @keyframes cvspin { to { transform: rotate(360deg); } }
    .cv-picker { position: absolute; display: none; pointer-events: auto; z-index: 5;
      background: #10241d; border: 1px solid #2ee6a0; border-radius: 10px; padding: 9px;
      box-shadow: 0 10px 30px rgba(0,0,0,.6); width: 306px; }
    .cv-picker .cv-title { font-size: 12px; color: #9dd8c1; margin-bottom: 7px; }
    .cv-picker .cv-group-title { font-size: 11px; font-weight: 700; margin: 5px 0 3px; }
    .cv-picker .cv-group-title.white { color: #fff; }
    .cv-picker .cv-group-title.black { color: #0b0b0b; background: #cfd8d3;
      border-radius: 4px; padding: 1px 5px; display: inline-block; }
    .cv-picker .cv-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 3px; }
    .cv-picker button { background: #1c3a30; border: 1px solid #35705c; color: #fff;
      border-radius: 5px; height: 40px; cursor: pointer; font-size: 19px; line-height: 1; padding: 2px 0 0;
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; }
    .cv-picker button:hover { background: #2ee6a0; color: #062016; }
    .cv-picker button.cv-white-piece { color: #fff; text-shadow: 0 0 3px #000; }
    .cv-picker button.cv-black-piece { color: #101010; background: #d9e2dd; border-color: #94a39c; }
    .cv-picker button.cv-black-piece:hover { background: #2ee6a0; color: #062016; }
    .cv-picker .cv-letter { font-size: 9px; opacity: .85; }
    .cv-picker .cv-actions { display: flex; gap: 6px; margin-top: 8px; }
    .cv-picker .cv-actions button { flex: 1; height: auto; padding: 6px; font-size: 12px; }
    .cv-toast { position: fixed; right: 18px; bottom: 18px; left: auto; transform: none;
      background: #10241d; border: 1px solid #2ee6a0; border-radius: 10px; padding: 10px 16px;
      font-size: 13px; pointer-events: none; box-shadow: 0 8px 30px rgba(0,0,0,.5); }
  `;

  let shadow = null;
  let frame = null;
  let gridLayer = null;
  let svgLayer = null;
  let badge = null;
  let picker = null;
  let state = null;
  let correctionMode = false;
  let correctBoard = null;
  let changedCells = new Set();
  let selecting = false;
  let reposition = null;
  let toastTimer = null;
  let applyTimer = null;
  let watchLive = false;

  function ensureHost() {
    if (shadow) return shadow;
    const host = document.createElement('div');
    host.id = 'chessvision-host';
    document.documentElement.appendChild(host);
    shadow = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = CSS;
    shadow.appendChild(style);
    return shadow;
  }

  function post(message) {
    try {
      chrome.runtime.sendMessage(message);
    } catch (error) {
      showToast('ChessVision: ' + error.message);
    }
  }

  function showToast(text) {
    ensureHost();
    const old = shadow.querySelector('.cv-toast');
    if (old) old.remove();
    const toast = document.createElement('div');
    toast.className = 'cv-toast';
    toast.textContent = text;
    shadow.appendChild(toast);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.remove(), 3200);
  }

  /* ---------------------------- Brett markieren ---------------------------- */

  function startSelection() {
    ensureHost();
    clearOverlay();
    post({ type: 'WATCH_PAUSE', paused: true });
    selecting = true;
    const veil = document.createElement('div');
    veil.className = 'cv-veil';
    veil.innerHTML =
      '<div class="cv-help">Rechteck <b>genau um die 64 Felder</b> ziehen – der Rest der Seite ist egal.<br>' +
      'Danach läuft die Analyse automatisch weiter. (Esc = abbrechen)</div>' +
      '<div class="cv-box"><div class="cv-size"></div></div>';
    shadow.appendChild(veil);
    const box = veil.querySelector('.cv-box');
    const size = veil.querySelector('.cv-size');
    const help = veil.querySelector('.cv-help');
    let start = null;

    function draw(e) {
      if (!start) return;
      if (help) help.style.display = 'none';
      const x = Math.min(start.x, e.clientX);
      const y = Math.min(start.y, e.clientY);
      const w = Math.abs(e.clientX - start.x);
      const h = Math.abs(e.clientY - start.y);
      box.style.left = x + 'px';
      box.style.top = y + 'px';
      box.style.width = w + 'px';
      box.style.height = h + 'px';
      size.textContent = `${Math.round(w)} × ${Math.round(h)} px`;
    }

    function onDown(e) {
      if (e.button !== 0) return;
      e.preventDefault();
      start = { x: e.clientX, y: e.clientY };
      box.style.display = 'block';
      draw(e);
    }
    function onMove(e) { draw(e); }
    function onUp(e) {
      if (!start) return;
      const rect = {
        x: Math.min(start.x, e.clientX),
        y: Math.min(start.y, e.clientY),
        width: Math.abs(e.clientX - start.x),
        height: Math.abs(e.clientY - start.y)
      };
      cleanup();
      if (rect.width >= 80 && rect.height >= 80) {
        post({
          type: 'BOARD_SELECTED',
          rect,
          dpr: window.devicePixelRatio || 1
        });
      } else {
        post({ type: 'WATCH_PAUSE', paused: false });
        showToast('Bitte ein größeres Rechteck um das Brett ziehen.');
      }
    }
    function onKey(e) {
      if (e.key === 'Escape') {
        cleanup();
        post({ type: 'WATCH_PAUSE', paused: false });
        showToast('Markierung abgebrochen.');
      }
    }
    function cleanup() {
      veil.removeEventListener('mousedown', onDown);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('keydown', onKey);
      veil.remove();
      selecting = false;
      start = null;
    }
    veil.addEventListener('mousedown', onDown);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('keydown', onKey);
  }

  /* ------------------------------- Overlay -------------------------------- */

  function clearOverlay() {
    if (!shadow) return;
    clearTimeout(applyTimer);
    applyTimer = null;
    if (reposition) {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
      reposition = null;
    }
    if (frame) frame.remove();
    frame = gridLayer = svgLayer = badge = picker = null;
    state = null;
    correctionMode = false;
    correctBoard = null;
    changedCells = new Set();
  }

  function squareFromScreen(r, c, orientation) {
    const file = orientation === 'black' ? 7 - c : c;
    const rank = orientation === 'black' ? r : 7 - r;
    return rank * 8 + file;
  }

  function squareName(square) {
    return FILES[square % 8] + (Math.floor(square / 8) + 1);
  }

  function centerOf(square, rect, orientation) {
    const file = square % 8;
    const rank = Math.floor(square / 8);
    const c = orientation === 'black' ? 7 - file : file;
    const r = orientation === 'black' ? rank : 7 - rank;
    return {
      x: rect.width * (c + 0.5) / 8,
      y: rect.height * (r + 0.5) / 8
    };
  }

  function ensureFrame() {
    ensureHost();
    if (frame) return;
    frame = document.createElement('div');
    frame.className = 'cv-frame';
    gridLayer = document.createElement('div');
    gridLayer.className = 'cv-grid';
    svgLayer = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svgLayer.setAttribute('class', 'cv-svg');
    badge = document.createElement('div');
    badge.className = 'cv-badge';
    picker = document.createElement('div');
    picker.className = 'cv-picker';
    frame.append(gridLayer, svgLayer, badge, picker);
    shadow.appendChild(frame);
    reposition = () => updateFramePosition();
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
  }

  function updateFramePosition() {
    if (!frame || !state) return;
    const rect = state.rect;
    frame.style.width = rect.width + 'px';
    frame.style.height = rect.height + 'px';
    frame.style.left = (state.pageX - window.scrollX) + 'px';
    frame.style.top = (state.pageY - window.scrollY) + 'px';
  }

  function render() {
    if (!state) return;
    ensureFrame();
    updateFramePosition();
    gridLayer.innerHTML = '';
    svgLayer.innerHTML = '';
    frame.querySelectorAll('.cv-help').forEach(node => node.remove());
    picker.style.display = 'none';
    if (correctionMode) renderCorrection();
    else renderBadge();
  }

  function renderBadge() {
    const result = state.result;
    const busy = state.busy;
    const error = state.error;
    let main;
    if (busy) {
      main = `<span class="cv-spinner"></span><span>${escapeHtml(busy)}</span>`;
    } else if (error) {
      main = `<span style="color:#ffb4a8">⚠ ${escapeHtml(error)}</span>`;
    } else if (result && result.bestmove) {
      const evalText = result.mate != null
        ? (result.mate > 0 ? `Matt in ${result.mate}` : `Matt in ${-result.mate} (gegen dich)`)
        : (result.cp != null ? (result.cp >= 0 ? '+' : '') + (result.cp / 100).toFixed(2) : '');
      main = `<span class="cv-move">${escapeHtml(result.san || result.bestmove)}</span>` +
        `<span style="color:#9dd8c1">${escapeHtml(result.bestmove)}</span>` +
        `<span class="cv-eval">${escapeHtml(evalText)}</span>`;
    } else {
      main = '<span>Kein Zug gefunden</span>';
    }
    badge.innerHTML =
      `<span class="cv-live ${watchLive ? 'on' : ''}">${watchLive ? '● LIVE' : '○ LIVE'}</span>` +
      `<span class="cv-side" title="Analysierte Zugfarbe">${state.turn === 'b' ? 'Schwarz' : 'Weiß'}</span>` +
      main +
      `<button data-act="turn" title="Zugfarbe wechseln (Weiß/Schwarz)">W↔B</button>` +
      `<button data-act="live" class="${watchLive ? 'live-on' : ''}" title="Live-Analyse ein/aus">${watchLive ? '⏸' : '▶'}</button>` +
      '<button data-act="reselect" title="Brett neu markieren">⬚</button>' +
      '<button data-act="refresh" title="Neu analysieren">↻</button>' +
      '<button data-act="correct" title="Figuren korrigieren">✎</button>' +
      '<button data-act="flip" title="Farben tauschen / Brett drehen">⇄</button>' +
      '<button data-act="close" title="Overlay und Live-Analyse beenden">✕</button>';
    badge.querySelectorAll('button').forEach(button => {
      button.addEventListener('click', () => {
        const act = button.dataset.act;
        if (act === 'live') post({ type: 'TOGGLE_WATCH' });
        else if (act === 'turn') post({ type: 'TOGGLE_TURN' });
        else if (act === 'reselect') startSelection();
        else if (act === 'refresh') post({ type: 'REANALYZE_LATEST' });
        else if (act === 'correct') enterCorrection();
        else if (act === 'flip') post({ type: 'FLIP_ORIENTATION' });
        else {
          post({ type: 'WATCH_STOP' });
          clearOverlay();
        }
      });
    });
    if (result && result.bestmove && (!state.uncertain || state.uncertain.length === 0)) {
      drawArrow(result.bestmove);
    }
    if (state.uncertain && state.uncertain.length) {
      const hint = document.createElement('div');
      hint.className = 'cv-help';
      hint.style.cssText = 'top:auto;bottom:-54px;font-size:12px;padding:5px 10px';
      hint.textContent = '⚠ Manche Figuren sind unsicher – bei Bedarf mit ✎ korrigieren.';
      frame.appendChild(hint);
    }
  }

  function drawArrow(uci) {
    const from = uci.slice(0, 2);
    const to = uci.slice(2, 4);
    const parse = name => {
      const file = FILES.indexOf(name[0]);
      const rank = Number(name[1]) - 1;
      return rank * 8 + file;
    };
    const p1 = centerOf(parse(from), state.rect, state.orientation);
    const p2 = centerOf(parse(to), state.rect, state.orientation);
    const width = Math.max(5, state.rect.width / 90);
    svgLayer.innerHTML =
      '<defs><marker id="cv-arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">' +
      '<path d="M0,0 L8,4 L0,8 z" fill="#00e676"/></marker></defs>' +
      `<rect x="${p1.x - state.rect.width / 16}" y="${p1.y - state.rect.height / 16}" ` +
      `width="${state.rect.width / 8}" height="${state.rect.height / 8}" fill="rgba(0,230,118,.18)"/>` +
      `<line x1="${p1.x}" y1="${p1.y}" x2="${p2.x}" y2="${p2.y}" stroke="#00e676" ` +
      `stroke-width="${width}" stroke-linecap="round" marker-end="url(#cv-arrowhead)"/>`;
  }

  /* -------------------------- Manuelle Korrektur --------------------------- */

  function enterCorrection() {
    correctionMode = true;
    correctBoard = state.board.slice();
    changedCells = new Set();
    post({ type: 'WATCH_PAUSE', paused: true });
    render();
  }

  function applyCorrection() {
    if (!correctionMode || !state) return;
    clearTimeout(applyTimer);
    applyTimer = null;
    post({
      type: 'BOARD_CORRECTED',
      board: correctBoard,
      changedIndices: [...changedCells],
      requestId: state.requestId
    });
    correctionMode = false;
    state.busy = 'Neue Stellung wird analysiert…';
    state.result = null;
    render();
  }

  function scheduleCorrection() {
    clearTimeout(applyTimer);
    applyTimer = setTimeout(applyCorrection, 900);
  }

  function renderCorrection() {
    badge.innerHTML =
      '<span class="cv-move" style="font-size:14px">Figur korrigieren</span>' +
      `<span class="cv-live ${applyTimer ? 'on' : ''}">${applyTimer ? '✓ wird übernommen…' : 'Weiß/Schwarz wählen'}</span>` +
      '<button data-act="apply" title="Korrigierte Stellung sofort analysieren">✓ Übernehmen</button>' +
      '<button data-act="reset" title="Erkennung wiederherstellen">↺</button>' +
      '<button data-act="cancel" title="Abbrechen">✕</button>';
    badge.querySelectorAll('button').forEach(button => {
      button.addEventListener('click', () => {
        const act = button.dataset.act;
        if (act === 'apply') {
          applyCorrection();
        } else if (act === 'reset') {
          clearTimeout(applyTimer);
          applyTimer = null;
          correctBoard = state.board.slice();
          changedCells.clear();
          render();
        } else {
          clearTimeout(applyTimer);
          applyTimer = null;
          correctionMode = false;
          post({ type: 'WATCH_PAUSE', paused: false });
          render();
        }
      });
    });

    const cw = state.rect.width / 8;
    const ch = state.rect.height / 8;
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const idx = r * 8 + c;
        const square = squareFromScreen(r, c, state.orientation);
        const symbol = correctBoard[square];
        const cell = document.createElement('div');
        cell.className = 'cv-cell' +
          (symbol ? (symbol === symbol.toUpperCase() ? ' cv-white' : ' cv-black') : '') +
          (changedCells.has(idx) ? ' changed' : '');
        cell.style.cssText = `left:${c * cw}px;top:${r * ch}px;width:${cw}px;height:${ch}px`;
        if (symbol) {
          const span = document.createElement('span');
          span.textContent = GLYPHS[symbol] || '';
          cell.appendChild(span);
        }
        cell.title = squareName(square);
        cell.addEventListener('click', () => openPicker(idx, square, c * cw, r * ch, cw, ch));
        gridLayer.appendChild(cell);
      }
    }
  }

  function pieceButton(piece) {
    const white = piece === piece.toUpperCase();
    return `<button data-piece="${piece}" class="${white ? 'cv-white-piece' : 'cv-black-piece'}" ` +
      `title="${white ? 'Weiß' : 'Schwarz'} ${piece}">` +
      `<span>${GLYPHS[piece]}</span><span class="cv-letter">${piece}</span></button>`;
  }

  function openPicker(index, square, x, y, cw, ch) {
    picker.innerHTML =
      '<div class="cv-title">Figur auf ' + squareName(square) + '</div>' +
      '<div class="cv-group-title white">Weiß</div>' +
      '<div class="cv-row">' + PIECES.filter(p => p === p.toUpperCase()).map(pieceButton).join('') + '</div>' +
      '<div class="cv-group-title black">Schwarz</div>' +
      '<div class="cv-row">' + PIECES.filter(p => p === p.toLowerCase()).map(pieceButton).join('') + '</div>' +
      '<div class="cv-actions">' +
      '<button data-act="toggle-color" title="Farbe dieser Figur wechseln">◐ Farbe wechseln</button>' +
      '<button data-piece="" title="Feld leeren">leer</button>' +
      '</div>';
    picker.style.display = 'block';
    picker.style.left = Math.max(0, Math.min(x - 150, state.rect.width - 322)) + 'px';
    picker.style.top = Math.max(0, Math.min(state.rect.height - 200, y + ch / 2)) + 'px';
    picker.querySelectorAll('button').forEach(button => {
      button.addEventListener('click', () => {
        const act = button.dataset.act;
        if (act === 'toggle-color') {
          const current = correctBoard[square];
          correctBoard[square] = current
            ? (current === current.toUpperCase() ? current.toLowerCase() : current.toUpperCase())
            : 'P';
        } else {
          correctBoard[square] = button.dataset.piece || null;
        }
        changedCells.add(index);
        picker.style.display = 'none';
        scheduleCorrection();
        render();
      });
    });
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }

  /* ----------------------------- Nachrichten ------------------------------ */

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || typeof message.type !== 'string') return;
    switch (message.type) {
      case 'START_SELECTION':
        startSelection();
        break;
      case 'WATCH_STATE':
        watchLive = !!message.live;
        if (state && !correctionMode) render();
        break;
      case 'CAPTURE_PREPARE':
        // Nur der Pfeil wird kurz ausgeblendet, damit die Live-Erkennung nicht
        // die eigene Overlay-Grafik als Figur misinterpretiert.
        if (svgLayer) svgLayer.style.visibility = 'hidden';
        if (gridLayer) gridLayer.style.visibility = 'hidden';
        setTimeout(() => {
          try { sendResponse({ ok: true }); } catch (_) { /* ignorieren */ }
        }, 40);
        return true;
      case 'CAPTURE_RESTORE':
        if (svgLayer) svgLayer.style.visibility = '';
        if (gridLayer) gridLayer.style.visibility = '';
        break;
      case 'STATUS':
        if (state && !correctionMode) {
          state.busy = message.text;
          state.error = null;
          render();
        }
        break;
      case 'POSITION':
        state = Object.assign({}, message, {
          pageX: message.rect.x + window.scrollX,
          pageY: message.rect.y + window.scrollY,
          result: null,
          busy: null,
          error: null
        });
        correctionMode = false;
        render();
        break;
      case 'RESULT':
        if (state) {
          state.result = message;
          state.busy = null;
          state.error = null;
          if (!correctionMode) render();
        }
        break;
      case 'ERROR':
        if (state) {
          state.busy = null;
          state.error = message.message;
          if (!correctionMode) render();
        } else {
          showToast(message.message);
        }
        break;
      case 'CLEAR_OVERLAY':
      case 'BOARD_SELECTED':
        if (!selecting) clearOverlay();
        break;
    }
  });
})();
