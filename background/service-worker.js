/* ChessVision Service Worker: koordiniert Markierung, Screenshot, Erkennung und Engine. */

const DEFAULTS = { orientation: 'white', turn: 'w', timeMs: 1200 };
const OFFSCREEN_URL = chrome.runtime.getURL('engine/host.html');
const JUSTIFICATION =
  'Erkennt die Schachstellung aus dem markierten Screenshot und betreibt die lokal eingebettete Stockfish-Engine.';

let creatingOffscreen = null;
const requestTabs = new Map();

async function getSettings() {
  const saved = await chrome.storage.session.get('settings');
  return Object.assign({}, DEFAULTS, saved.settings || {});
}

async function getLastByTab() {
  const saved = await chrome.storage.session.get('lastByTab');
  return saved.lastByTab || {};
}

async function saveLast(tabId, data) {
  const all = await getLastByTab();
  all[tabId] = data;
  await chrome.storage.session.set({ lastByTab: all });
}

async function ensureOffscreen() {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (contexts.length > 0) return;
  if (creatingOffscreen) return creatingOffscreen;
  creatingOffscreen = chrome.offscreen
    .createDocument({ url: OFFSCREEN_URL, reasons: ['DOM_SCRAPING', 'WORKERS'], justification: JUSTIFICATION })
    .catch(() => chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ['DOM_SCRAPING'],
      justification: JUSTIFICATION
    }))
    .finally(() => { creatingOffscreen = null; });
  await creatingOffscreen;
}

async function toHost(message) {
  await ensureOffscreen();
  chrome.runtime.sendMessage(message);
}

function toTab(tabId, message) {
  if (tabId == null) return;
  chrome.tabs.sendMessage(tabId, message).catch(() => {});
}

async function forward(message, tabId) {
  toTab(tabId, message);
  chrome.runtime.sendMessage(message).catch(() => {});
  if (message.type === 'POSITION' || message.type === 'RESULT' || message.type === 'ERROR') {
    const last = await getLastByTab();
    const previous = last[tabId] || {};
    const patch = message.type === 'RESULT' ? { result: message } : message;
    await saveLast(tabId, Object.assign({}, previous, patch, { tabId }));
  }
}

function nextRequestId(tabId) {
  const id = `${tabId}:${Date.now()}:${Math.random().toString(36).slice(2, 7)}`;
  requestTabs.set(id, tabId);
  return id;
}

async function analyzeScreenshot(message, sender) {
  const tabId = sender.tab && sender.tab.id;
  if (tabId == null) return;
  try {
    const settings = await getSettings();
    const imageDataUrl = await chrome.tabs.captureVisibleTab(tabId, { format: 'png' });
    const requestId = nextRequestId(tabId);
    const rect = message.rect;
    await saveLast(tabId, { rect, dpr: message.dpr, requestId, tabId, orientation: settings.orientation });
    forward({ type: 'STATUS', text: 'Brett erkannt – Figuren werden gelesen…', requestId, tabId }, tabId);
    await toHost({
      type: 'ANALYZE',
      requestId,
      tabId,
      imageDataUrl,
      rect,
      dpr: message.dpr || 1,
      settings
    });
  } catch (error) {
    forward({ type: 'ERROR', message: 'Screenshot fehlgeschlagen: ' + error.message, tabId }, tabId);
  }
}

async function rebuildFromGrid(tabId, grid, reason) {
  const settings = await getSettings();
  const requestId = nextRequestId(tabId);
  const last = await getLastByTab();
  await saveLast(tabId, Object.assign({}, last[tabId] || {}, { requestId, grid, tabId, orientation: settings.orientation }));
  forward({ type: 'STATUS', text: reason || 'Stellung wird neu aufgebaut…', requestId, tabId }, tabId);
  await toHost({ type: 'HOST_REBUILD_FROM_GRID', requestId, tabId, grid, settings });
}

async function flip(tabId) {
  const settings = await getSettings();
  settings.orientation = settings.orientation === 'white' ? 'black' : 'white';
  settings.turn = settings.orientation === 'white' ? 'w' : 'b';
  await chrome.storage.session.set({ settings });
  chrome.runtime.sendMessage({ type: 'SETTINGS', settings }).catch(() => {});
  const last = await getLastByTab();
  if (last[tabId] && last[tabId].grid) {
    await rebuildFromGrid(tabId, last[tabId].grid, 'Brett wird gedreht…');
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== 'string') return false;
  const tabId = sender.tab ? sender.tab.id : message.tabId;

  (async () => {
    switch (message.type) {
      case 'BOARD_SELECTED':
        await analyzeScreenshot(message, sender);
        break;

      case 'BOARD_CORRECTED': {
        const settings = await getSettings();
        const requestId = message.requestId || nextRequestId(tabId);
        forward({ type: 'STATUS', text: 'Korrigierte Stellung wird analysiert…', requestId, tabId }, tabId);
        await toHost({
          type: 'HOST_SET_BOARD',
          requestId,
          tabId,
          board: message.board,
          changedIndices: message.changedIndices || [],
          settings
        });
        break;
      }

      case 'REANALYZE_LATEST': {
        const last = await getLastByTab();
        const entry = last[tabId];
        if (!entry) {
          forward({ type: 'ERROR', message: 'Bitte zuerst ein Brett markieren.', tabId }, tabId);
          break;
        }
        const settings = await getSettings();
        const requestId = nextRequestId(tabId);
        forward({ type: 'STATUS', text: 'Stockfish rechnet…', requestId, tabId }, tabId);
        if (entry.board) {
          await toHost({ type: 'HOST_SET_BOARD', requestId, tabId, board: entry.board, changedIndices: [], settings });
        } else if (entry.grid) {
          await rebuildFromGrid(tabId, entry.grid, 'Stockfish rechnet…');
        } else {
          forward({ type: 'ERROR', message: 'Keine Stellung gespeichert – bitte neu markieren.', tabId }, tabId);
        }
        break;
      }

      case 'FLIP_ORIENTATION':
        await flip(tabId);
        break;

      case 'SETTINGS_CHANGED': {
        const old = await getSettings();
        const settings = Object.assign({}, old, message.settings || {});
        await chrome.storage.session.set({ settings });
        chrome.runtime.sendMessage({ type: 'SETTINGS', settings }).catch(() => {});
        if (old.orientation !== settings.orientation) {
          const last = await getLastByTab();
          if (last[tabId] && last[tabId].grid) await rebuildFromGrid(tabId, last[tabId].grid, 'Brett wird gedreht…');
        }
        break;
      }

      case 'GET_STATE': {
        const settings = await getSettings();
        const last = await getLastByTab();
        sendResponse({ type: 'STATE', settings, last: last[tabId] || null });
        break;
      }

      case 'CLEAR_BOARD': {
        const last = await getLastByTab();
        delete last[tabId];
        await chrome.storage.session.set({ lastByTab: last });
        toTab(tabId, { type: 'CLEAR_OVERLAY' });
        break;
      }
    }
  })().catch(error => {
    forward({ type: 'ERROR', message: error.message, tabId }, tabId);
    if (message.type === 'GET_STATE') sendResponse({ type: 'STATE', settings: DEFAULTS, last: null });
  });

  return message.type === 'GET_STATE';
});

/* Antworten des Offscreen-Dokuments an Tab und Popup weiterleiten. */
chrome.runtime.onMessage.addListener(message => {
  if (!message || typeof message.type !== 'string') return;
  if (!['STATUS', 'POSITION', 'RESULT', 'ERROR'].includes(message.type)) return;
  const tabId = message.tabId != null ? message.tabId : requestTabs.get(message.requestId);
  if (tabId == null) return;
  forward(message, tabId);
});

chrome.tabs.onRemoved.addListener(async tabId => {
  const last = await getLastByTab();
  if (last[tabId]) {
    delete last[tabId];
    await chrome.storage.session.set({ lastByTab: last });
  }
});
