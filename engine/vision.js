/* Figurenerkennung aus einem Screenshot: 8x8-Raster, Farbe und Figur pro Feld. */

import { TEMPLATE_TO_SYMBOL } from './chess-utils.js';

const SOLID_GLYPHS = {
  P: '♟', N: '♞', B: '♝', R: '♜', Q: '♛', K: '♚',
  p: '♟', n: '♞', b: '♝', r: '♜', q: '♛', k: '♚'
};
const OUTLINE_GLYPHS = {
  P: '♙', N: '♘', B: '♗', R: '♖', Q: '♕', K: '♔',
  p: '♙', n: '♘', b: '♗', r: '♖', q: '♕', k: '♔'
};
const FONTS = ['"DejaVu Sans", sans-serif', 'serif', '"Segoe UI Symbol", sans-serif'];

/* ----------------------------- Bild-Helfer -------------------------------- */

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Bild konnte nicht geladen werden: ' + url));
    image.src = url;
  });
}

function median(values) {
  if (!values.length) return 0;
  const copy = Array.from(values).sort((a, b) => a - b);
  const mid = copy.length >> 1;
  return copy.length % 2 ? copy[mid] : (copy[mid - 1] + copy[mid]) / 2;
}

function slice(source, srcW, x0, y0, width, height) {
  const out = new Float32Array(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) out[y * width + x] = source[(y0 + y) * srcW + (x0 + x)];
  }
  return out;
}

function blur3(source, w, h) {
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let sum = 0;
      let count = 0;
      for (let dy = -1; dy <= 1; dy++) {
        const yy = y + dy;
        if (yy < 0 || yy >= h) continue;
        for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx;
          if (xx < 0 || xx >= w) continue;
          sum += source[yy * w + xx];
          count++;
        }
      }
      out[y * w + x] = sum / count;
    }
  }
  return out;
}

/* Canny-Kantendetektor (Sobel + Non-Maximum-Suppression + Hysterese). */
function canny(gray, w, h, low = 60, high = 160) {
  const magnitude = new Float32Array(w * h);
  const gxValues = new Float32Array(w * h);
  const gyValues = new Float32Array(w * h);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const a = gray[i - w - 1], b = gray[i - w], c = gray[i - w + 1];
      const d = gray[i - 1], f = gray[i + 1];
      const g = gray[i + w - 1], j = gray[i + w], k = gray[i + w + 1];
      const gx = -a + c - 2 * d + 2 * f - g + k;
      const gy = -a - 2 * b - c + g + 2 * j + k;
      gxValues[i] = gx;
      gyValues[i] = gy;
      magnitude[i] = Math.hypot(gx, gy);
    }
  }
  const out = new Uint8Array(w * h);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const value = magnitude[i];
      if (value < low || out[i]) continue;
      const angle = Math.atan2(gyValues[i], gxValues[i]) * 180 / Math.PI;
      let n1, n2;
      if ((angle >= -22.5 && angle < 22.5) || angle >= 157.5 || angle < -157.5) {
        n1 = magnitude[i - 1]; n2 = magnitude[i + 1];
      } else if (angle >= 22.5 && angle < 67.5) {
        n1 = magnitude[i - w + 1]; n2 = magnitude[i + w - 1];
      } else if (angle >= 67.5 && angle < 112.5) {
        n1 = magnitude[i - w]; n2 = magnitude[i + w];
      } else {
        n1 = magnitude[i - w - 1]; n2 = magnitude[i + w + 1];
      }
      if (value < n1 || value < n2) continue;
      if (value >= high) {
        out[i] = 255;
        continue;
      }
      const stack = [i];
      out[i] = 255;
      while (stack.length) {
        const current = stack.pop();
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            if (!dx && !dy) continue;
            const next = current + dy * w + dx;
            if (next < 0 || next >= out.length || out[next]) continue;
            if (magnitude[next] >= low) {
              out[next] = 255;
              stack.push(next);
            }
          }
        }
      }
    }
  }
  const edges = new Float32Array(w * h);
  for (let i = 0; i < out.length; i++) edges[i] = out[i];
  return blur3(edges, w, h);
}

function makeTemplate(pixels, symbol) {
  const alpha = new Float32Array(48 * 48);
  const comp = new Float32Array(48 * 48);
  for (let i = 0; i < 48 * 48; i++) {
    const a = pixels[i * 4 + 3] / 255;
    const luma = 0.299 * pixels[i * 4] + 0.587 * pixels[i * 4 + 1] + 0.114 * pixels[i * 4 + 2];
    alpha[i] = a;
    comp[i] = luma * a + 160 * (1 - a);
  }
  const edges = canny(slice(comp, 48, 4, 4, 40, 40), 40, 40);
  const gray36 = slice(comp, 48, 6, 6, 36, 36);
  const alpha36 = new Uint8Array(36 * 36);
  for (let y = 0; y < 36; y++) {
    for (let x = 0; x < 36; x++) alpha36[y * 36 + x] = alpha[(y + 6) * 48 + (x + 6)] > 0.3 ? 1 : 0;
  }
  return { symbol, gray: gray36, edges, alpha: alpha36 };
}

/** Neues Template aus einer vom Nutzer korrigierten Zelle lernen. */
export function makeLearnedTemplate(gray48, mask48, symbol) {
  const edges = canny(slice(gray48, 48, 4, 4, 40, 40), 40, 40);
  const alpha36 = new Uint8Array(36 * 36);
  for (let y = 0; y < 36; y++) {
    for (let x = 0; x < 36; x++) alpha36[y * 36 + x] = mask48[(y + 6) * 48 + (x + 6)] ? 1 : 0;
  }
  return { symbol, gray: slice(gray48, 48, 6, 6, 36, 36), edges, alpha: alpha36, learned: true };
}

function renderGlyph(glyph, isWhite, font, size) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 48;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.clearRect(0, 0, 48, 48);
  ctx.font = `${size}px ${font}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = isWhite ? 2.5 : 1.5;
  ctx.strokeStyle = isWhite ? '#222222' : '#101010';
  ctx.fillStyle = isWhite ? '#ffffff' : '#101010';
  ctx.strokeText(glyph, 24, 25);
  ctx.fillText(glyph, 24, 25);
  return ctx.getImageData(0, 0, 48, 48).data;
}

/** Chess.com-Neo-Sprites + Unicode-Glyphen als Vorlagen laden. */
export async function buildTemplates() {
  if (document.fonts && document.fonts.ready) {
    try { await document.fonts.ready; } catch (_) { /* ignorieren */ }
  }
  const templates = [];
  const codes = Object.keys(TEMPLATE_TO_SYMBOL);
  const images = await Promise.all(codes.map(code => loadImage(chrome.runtime.getURL(`sprites/${code}.png`))));
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 48;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  images.forEach((image, index) => {
    ctx.clearRect(0, 0, 48, 48);
    ctx.drawImage(image, 0, 0, 48, 48);
    templates.push(makeTemplate(ctx.getImageData(0, 0, 48, 48).data, TEMPLATE_TO_SYMBOL[codes[index]]));
  });

  const symbols = Object.values(TEMPLATE_TO_SYMBOL);
  for (const symbol of symbols) {
    const isWhite = symbol === symbol.toUpperCase();
    for (const font of FONTS) {
      for (const size of [36, 40]) {
        templates.push(makeTemplate(renderGlyph(SOLID_GLYPHS[symbol], isWhite, font, size), symbol));
      }
    }
    for (const font of FONTS.slice(0, 2)) {
      templates.push(makeTemplate(renderGlyph(OUTLINE_GLYPHS[symbol], isWhite, font, 36), symbol));
    }
  }
  return templates;
}

/* ------------------------------ Klassifikation ---------------------------- */

function correlation(a, b) {
  const n = a.length;
  let sumA = 0, sumB = 0;
  for (let i = 0; i < n; i++) { sumA += a[i]; sumB += b[i]; }
  const meanA = sumA / n, meanB = sumB / n;
  let num = 0, denA = 0, denB = 0;
  for (let i = 0; i < n; i++) {
    const x = a[i] - meanA;
    const y = b[i] - meanB;
    num += x * y;
    denA += x * x;
    denB += y * y;
  }
  return num / (Math.sqrt(denA * denB) || 1);
}

function intersectionOverUnion(a, b) {
  let intersection = 0, union = 0;
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = b[i];
    if (x && y) intersection++;
    if (x || y) union++;
  }
  return union ? intersection / union : 0;
}

function cellMask(gray48) {
  const samples = [];
  for (let y = 2; y < 7; y++) {
    for (let x = 2; x < 8; x++) samples.push(gray48[y * 48 + x]);
    for (let x = 40; x < 46; x++) samples.push(gray48[y * 48 + x]);
  }
  const background = median(samples);
  const mask = new Uint8Array(48 * 48);
  for (let y = 0; y < 48; y++) {
    for (let x = 0; x < 48; x++) {
      if (x < 3 || x >= 45 || y < 3 || y >= 45) continue;
      mask[y * 48 + x] = Math.abs(gray48[y * 48 + x] - background) > 28 ? 1 : 0;
    }
  }
  return { background, mask };
}

function classifySymbol(gray48, mask, row, templates, learned, colorHint) {
  const cellEdges = canny(slice(gray48, 48, 4, 4, 40, 40), 40, 40);
  const cellGray36 = slice(gray48, 48, 6, 6, 36, 36);
  const mask36 = new Uint8Array(36 * 36);
  for (let y = 0; y < 36; y++) {
    for (let x = 0; x < 36; x++) mask36[y * 36 + x] = mask[(y + 6) * 48 + (x + 6)];
  }
  let best = null, bestScore = -Infinity;
  const evaluate = template => {
    if ((template.symbol === 'P' || template.symbol === 'p') && (row === 0 || row === 7)) return;
    if (colorHint && (template.symbol === template.symbol.toUpperCase() ? 'w' : 'b') !== colorHint) return;
    const gray = correlation(cellGray36, template.gray);
    const edge = correlation(cellEdges, template.edges);
    const iou = intersectionOverUnion(mask36, template.alpha);
    const score = template.learned
      ? 0.55 * gray + 0.30 * edge + 0.15 * iou
      : 0.70 * gray + 0.15 * edge + 0.15 * iou;
    if (score > bestScore) { bestScore = score; best = template.symbol; }
  };
  for (const template of templates) evaluate(template);
  for (const key of Object.keys(learned)) {
    for (const template of learned[key]) evaluate(template);
  }
  return { symbol: best || 'p', score: bestScore };
}

/**
 * Analysiert den markierten Bereich.
 * @returns {{grid: Array<Array<string|null>>, cells: Array<object>, uncertain: number[]}}
 */
export async function analyzeBoardImage(dataUrl, rect, dpr, templates, learned) {
  const image = await loadImage(dataUrl);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 384;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  const sx = Math.max(0, Math.round(rect.x * dpr));
  const sy = Math.max(0, Math.round(rect.y * dpr));
  const sw = Math.min(image.width - sx, Math.max(1, Math.round(rect.width * dpr)));
  const sh = Math.min(image.height - sy, Math.max(1, Math.round(rect.height * dpr)));
  ctx.drawImage(image, sx, sy, sw, sh, 0, 0, 384, 384);
  const rgba = ctx.getImageData(0, 0, 384, 384).data;
  const grayImage = new Float32Array(384 * 384);
  for (let i = 0; i < grayImage.length; i++) {
    grayImage[i] = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2];
  }

  const grid = Array.from({ length: 8 }, () => Array(8).fill(null));
  const cells = Array(64);
  const uncertain = [];
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const gray48 = slice(grayImage, 384, c * 48, r * 48, 48, 48);
      const { mask } = cellMask(gray48);
      let foreground = 0;
      for (let y = 10; y < 38; y++) {
        for (let x = 10; x < 38; x++) foreground += mask[y * 48 + x];
      }
      const index = r * 8 + c;
      if (foreground < 28) {
        cells[index] = { gray48, mask48: mask, symbol: null, score: 0 };
        continue;
      }
      const center = [];
      for (let y = 17; y < 31; y++) {
        for (let x = 17; x < 31; x++) center.push(gray48[y * 48 + x]);
      }
      const centerLuma = median(center);
      const colorHint = centerLuma >= 195 ? 'w' : (centerLuma <= 135 ? 'b' : null);
      const result = classifySymbol(gray48, mask, r, templates, learned, colorHint);
      grid[r][c] = result.symbol;
      cells[index] = { gray48, mask48: mask, symbol: result.symbol, score: result.score };
      if (result.score < 0.18) uncertain.push(index);
    }
  }
  return { grid, cells, uncertain };
}
