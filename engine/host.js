/* Offscreen-Dokument: Bilderkennung + Stockfish. Läuft vollständig offline. */

import { Chess } from '../lib/chess.js';
import { analyzeBoardImage, buildTemplates, makeLearnedTemplate } from './vision.js';
import { gridToBoard, makeFen, squareFromScreen } from './chess-utils.js';

const contexts = new Map();   // tabId -> letzte analysierte Stellung
const learned = {};           // Figur -> selbst gelernte Vorlagen aus Korrekturen
let templatesPromise = null;
let engine = null;

function post(message) {
  chrome.runtime.sendMessage(message).catch(() => {});
}

function getTemplates() {
  if (!templatesPromise) templatesPromise = buildTemplates();
  return templatesPromise;
}

/* ----------------------------- Stockfish UCI ------------------------------ */

class Stockfish {
  constructor() {
    this.worker = new Worker(chrome.runtime.getURL('engine/stockfish-19-lite-single.js'));
    this.listeners = new Set();
    this.ready = false;
    this.readyWaiters = [];
    this.current = null;
    this.ignoreNextBestmove = false;
    this.worker.onmessage = event => this.onLine(String(event.data));
    this.worker.onerror = event => {
      post({ type: 'ERROR', message: 'Stockfish-Fehler: ' + (event.message || 'unbekannt') });
    };
    this.send('uci');
  }

  send(line) {
    this.worker.postMessage(line);
  }

  onLine(line) {
    if (line.startsWith('bestmove ') && this.ignoreNextBestmove) {
      this.ignoreNextBestmove = false;
      return;
    }
    for (const listener of this.listeners) listener(line);
    if (line === 'uciok') {
      this.send('isready');
    } else if (line === 'readyok') {
      this.ready = true;
      for (const resolve of this.readyWaiters.splice(0)) resolve();
    }
  }

  waitReady() {
    if (this.ready) return Promise.resolve();
    return new Promise(resolve => this.readyWaiters.push(resolve));
  }

  async analyze(fen, timeMs) {
    await this.waitReady();
    if (this.current) {
      this.listeners.delete(this.current.listener);
      this.ignoreNextBestmove = true;
      this.send('stop');
      this.current.reject(new Error('_analysis_cancelled'));
      this.current = null;
    }
    return new Promise((resolve, reject) => {
      let cp = null, mate = null, depth = 0, pv = '';
      const listener = line => {
        if (line.startsWith('info ')) {
          const d = /\bdepth (\d+)/.exec(line);
          if (d) depth = Number(d[1]);
          const s = /\bscore cp (-?\d+)/.exec(line);
          if (s) { cp = Number(s[1]); mate = null; }
          const m = /\bscore mate (-?\d+)/.exec(line);
          if (m) { mate = Number(m[1]); cp = null; }
          const p = /\bpv (\S+)/.exec(line);
          if (p) pv = p[1];
        } else if (line.startsWith('bestmove ')) {
          const match = /bestmove (\S+)/.exec(line);
          this.listeners.delete(listener);
          this.current = null;
          resolve({ bestmove: match && match[1] !== '(none)' ? match[1] : null, cp, mate, depth, pv });
        }
      };
      this.listeners.add(listener);
      this.current = { listener, reject };
      this.send('position fen ' + fen);
      this.send('go movetime ' + Math.max(100, Math.round(timeMs) || 1200));
    });
  }
}

function getEngine() {
  if (!engine) engine = new Stockfish();
  return engine;
}

/* ------------------------------ FEN-Aufbau -------------------------------- */

function tryFen(board, turn) {
  const fen = makeFen(board, turn);
  try {
    new Chess(fen);
    return { fen, turn };
  } catch (_) {
    return null;
  }
}

function resolveFen(board, preferredTurn) {
  const first = tryFen(board, preferredTurn);
  if (first) return first;
  const second = tryFen(board, preferredTurn === 'w' ? 'b' : 'w');
  if (second) return second;
  return { fen: null, error: 'Stellung ungültig – bitte die Figuren mit ✎ korrigieren.' };
}

/* ------------------------------ Analyse ----------------------------------- */

async function analyzePosition(context) {
  const { requestId, tabId, settings, board } = context;
  const resolved = resolveFen(board, settings.turn);
  context.fen = resolved.fen;
  contexts.set(tabId, context);

  post({
    type: 'POSITION',
    requestId,
    tabId,
    fen: resolved.fen,
    board,
    orientation: settings.orientation,
    rect: context.rect,
    uncertain: context.uncertain || []
  });

  if (!resolved.fen) {
    post({ type: 'ERROR', requestId, tabId, message: resolved.error });
    return;
  }

  post({ type: 'STATUS', requestId, tabId, text: 'Stockfish rechnet…' });
  try {
    const analysis = await getEngine().analyze(resolved.fen, settings.timeMs);
    const latest = contexts.get(tabId);
    if (!latest || latest.requestId !== requestId) return;

    let san = null;
    if (analysis.bestmove) {
      try {
        const chess = new Chess(resolved.fen);
        san = chess.move({
          from: analysis.bestmove.slice(0, 2),
          to: analysis.bestmove.slice(2, 4),
          promotion: analysis.bestmove[4] || 'q'
        }).san;
      } catch (_) {
        san = analysis.bestmove;
      }
    }
    post({
      type: 'RESULT',
      requestId,
      tabId,
      bestmove: analysis.bestmove,
      san,
      cp: analysis.cp,
      mate: analysis.mate,
      depth: analysis.depth,
      pv: analysis.pv,
      fen: resolved.fen
    });
  } catch (error) {
    if (error.message === '_analysis_cancelled') return;
    post({ type: 'ERROR', requestId, tabId, message: 'Analyse fehlgeschlagen: ' + error.message });
  }
}

async function handleAnalyze(message) {
  const { requestId, tabId, rect, dpr, settings, imageDataUrl } = message;
  post({ type: 'STATUS', requestId, tabId, text: 'Figuren werden erkannt…' });
  try {
    const vision = await analyzeBoardImage(imageDataUrl, rect, dpr, await getTemplates(), learned);
    const context = {
      requestId,
      tabId,
      rect,
      dpr,
      settings,
      grid: vision.grid,
      cells: vision.cells,
      uncertain: vision.uncertain,
      board: gridToBoard(vision.grid, settings.orientation)
    };
    await analyzePosition(context);
  } catch (error) {
    post({ type: 'ERROR', requestId, tabId, message: 'Bilderkennung fehlgeschlagen: ' + error.message });
  }
}

function handleBoard(message, board) {
  const { requestId, tabId, settings } = message;
  const previous = contexts.get(tabId) || {};

  for (const index of message.changedIndices || []) {
    const symbol = board[squareFromScreen(Math.floor(index / 8), index % 8, settings.orientation)];
    const cell = previous.cells && previous.cells[index];
    if (!symbol || !cell) continue;
    const key = symbol;
    const list = learned[key] || (learned[key] = []);
    if (list.length < 8) list.push(makeLearnedTemplate(cell.gray48, cell.mask48, symbol));
  }

  analyzePosition({
    requestId,
    tabId,
    settings,
    rect: previous.rect,
    dpr: previous.dpr,
    grid: previous.grid,
    cells: previous.cells,
    uncertain: [],
    board
  });
}

chrome.runtime.onMessage.addListener(message => {
  if (!message || typeof message.type !== 'string') return;
  switch (message.type) {
    case 'ANALYZE':
      handleAnalyze(message);
      break;
    case 'HOST_SET_BOARD':
      handleBoard(message, message.board);
      break;
    case 'HOST_REBUILD_FROM_GRID':
      handleBoard(message, gridToBoard(message.grid, message.settings.orientation));
      break;
  }
});
