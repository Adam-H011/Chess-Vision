/* Popup: Brett markieren, Einstellungen und Analyseergebnis anzeigen. */

const elements = {
  select: document.getElementById('select'),
  side: document.getElementById('side'),
  time: document.getElementById('time'),
  timeValue: document.getElementById('timeValue'),
  status: document.getElementById('status'),
  move: document.getElementById('move'),
  evaluation: document.getElementById('evaluation'),
  fen: document.getElementById('fen'),
  refresh: document.getElementById('refresh'),
  clear: document.getElementById('clear'),
  copy: document.getElementById('copy')
};

let tabId = null;

async function ping(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'PING' });
    return true;
  } catch (_) {
    return false;
  }
}

/* Nach einem Reload der Extension sind alte Content Scripts oft „verwaist“.
   Dann einfach frisch injizieren, statt vom Nutzer einen Seiten-Reload zu verlangen. */
async function ensureContent(tabId) {
  if (await ping(tabId)) return true;
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content/content.js'] });
    await new Promise(resolve => setTimeout(resolve, 150));
    return await ping(tabId);
  } catch (_) {
    return false;
  }
}

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs.length ? tabs[0].id : null;
}

function formatTime(ms) {
  return (ms / 1000).toFixed(1).replace('.', ',') + ' s';
}

function setStatus(text) {
  elements.status.textContent = text || 'Bereit';
}

function showResult(data) {
  if (!data || !data.bestmove) {
    elements.move.textContent = '–';
    elements.evaluation.textContent = 'Kein legaler Zug gefunden';
    return;
  }
  elements.move.textContent = data.san || data.bestmove;
  if (data.mate != null) {
    elements.evaluation.textContent = data.mate > 0 ? `Matt in ${data.mate}` : `Matt in ${-data.mate} für den Gegner`;
  } else if (data.cp != null) {
    elements.evaluation.textContent = `Bewertung ${data.cp >= 0 ? '+' : ''}${(data.cp / 100).toFixed(2)} · Tiefe ${data.depth || '?'}`;
  } else {
    elements.evaluation.textContent = `Zug ${data.bestmove}`;
  }
}

function applyState(state) {
  if (!state) return;
  if (state.settings) {
    elements.side.value = state.settings.orientation || 'white';
    elements.time.value = state.settings.timeMs || 1200;
    elements.timeValue.textContent = formatTime(Number(elements.time.value));
  }
  const last = state.last;
  if (last) {
    if (last.fen) elements.fen.textContent = last.fen;
    if (last.result) showResult(last.result);
    if (last.busy) setStatus(last.busy);
    else if (last.error) setStatus(last.error);
    else if (last.fen) setStatus('Stellung erkannt');
  }
}

async function settingsChanged(patch) {
  await chrome.runtime.sendMessage({ type: 'SETTINGS_CHANGED', tabId, settings: patch });
}

elements.select.addEventListener('click', async () => {
  try {
    const target = tabId || (await activeTab());
    if (!(await ensureContent(target))) {
      setStatus('Markierung hier nicht möglich (z. B. chrome://-Seite).');
      return;
    }
    // Erst zustellen, dann schließen – sonst kann die Nachricht verloren gehen.
    await chrome.tabs.sendMessage(target, { type: 'START_SELECTION' });
    window.close();
  } catch (error) {
    setStatus('Markierung nicht möglich. Bitte die Seite neu laden und erneut versuchen.');
  }
});

elements.refresh.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'REANALYZE_LATEST', tabId });
  setStatus('Starte Analyse…');
});

elements.clear.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'CLEAR_BOARD', tabId });
  elements.fen.textContent = '–';
  elements.move.textContent = '–';
  elements.evaluation.textContent = 'Noch keine Analyse';
  setStatus('Bereit');
});

elements.copy.addEventListener('click', async () => {
  const text = elements.fen.textContent;
  if (text && text !== '–') {
    try { await navigator.clipboard.writeText(text); setStatus('FEN kopiert'); } catch (_) { /* ignorieren */ }
  }
});

elements.side.addEventListener('change', () => {
  settingsChanged({
    orientation: elements.side.value,
    turn: elements.side.value === 'white' ? 'w' : 'b'
  });
});

elements.time.addEventListener('input', () => {
  elements.timeValue.textContent = formatTime(Number(elements.time.value));
});
elements.time.addEventListener('change', () => {
  settingsChanged({ timeMs: Number(elements.time.value) });
});

chrome.runtime.onMessage.addListener(message => {
  if (!message || typeof message.type !== 'string') return;
  switch (message.type) {
    case 'STATUS':
      setStatus(message.text);
      break;
    case 'POSITION':
      elements.fen.textContent = message.fen || 'ungültige Stellung';
      elements.move.textContent = '…';
      elements.evaluation.textContent = 'Stockfish rechnet…';
      setStatus('Stellung erkannt');
      break;
    case 'RESULT':
      showResult(message);
      setStatus('Analyse fertig');
      break;
    case 'ERROR':
      setStatus(message.message);
      break;
    case 'SETTINGS':
      if (message.settings) {
        elements.side.value = message.settings.orientation;
        elements.time.value = message.settings.timeMs;
        elements.timeValue.textContent = formatTime(message.settings.timeMs);
      }
      break;
  }
});

(async () => {
  tabId = await activeTab();
  chrome.runtime.sendMessage({ type: 'GET_STATE', tabId }, response => {
    if (response && response.type === 'STATE') applyState(response);
  });
})();
