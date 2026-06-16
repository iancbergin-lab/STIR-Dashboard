// Polygon.io futures proxy — server-side to keep API key out of the browser.
// POST { product: 'ZQ' | 'SR3' }  →  [{ symbol, settle, implied_rate, expiry }]

const POLYGON_BASE = 'https://api.polygon.io';

// CME month codes
const MC = { 1:'F',2:'G',3:'H',4:'J',5:'K',6:'M',7:'N',8:'Q',9:'U',10:'V',11:'X',12:'Z' };
const MC_INV = Object.fromEntries(Object.entries(MC).map(([k,v])=>[v,+k]));

function addM(y, m, n) {
  m += n;
  while (m > 12) { m -= 12; y++; }
  return { y, m };
}

function dim(y, m) {
  return new Date(y, m, 0).getDate();
}

// Build list of ZQ tickers: monthly, current month + 18 months
function zqTickers() {
  const now = new Date();
  let y = now.getFullYear(), m = now.getMonth() + 1;
  const tickers = [];
  for (let i = 0; i < 18; i++) {
    tickers.push(`ZQ${MC[m]}${y % 10}`);
    const n = addM(y, m, 1); y = n.y; m = n.m;
  }
  return tickers;
}

// Build list of SR3 tickers: quarterly IMM (Mar/Jun/Sep/Dec), next 8 contracts
function sr3Tickers() {
  const now = new Date();
  let y = now.getFullYear(), m = now.getMonth() + 1;
  // Advance to next or current IMM quarter
  while (![3, 6, 9, 12].includes(m)) { const n = addM(y, m, 1); y = n.y; m = n.m; }
  const tickers = [];
  for (let i = 0; i < 8; i++) {
    tickers.push(`SR3${MC[m]}${y % 10}`);
    let n = addM(y, m, 3); y = n.y; m = n.m;
    while (![3, 6, 9, 12].includes(m)) { n = addM(y, m, 1); y = n.y; m = n.m; }
  }
  return tickers;
}

// Derive expiry date from CME symbol (last trading day = last business day of month for ZQ;
// for SR3 it's the 3rd Wednesday of the month — we use month-end as a close approximation
// for sorting/charting. Polygon's expiration_date is authoritative when present.
function symbolToExpiry(sym) {
  const root = sym.replace(/[A-Z]\d$/, '');
  const mc = sym[sym.length - 2];
  const yDigit = parseInt(sym[sym.length - 1], 10);
  const now = new Date();
  const decade = Math.floor(now.getFullYear() / 10) * 10;
  const year = yDigit + decade < now.getFullYear() - 1 ? yDigit + decade + 10 : yDigit + decade;
  const month = MC_INV[mc];
  return new Date(year, month - 1, dim(year, month)).toISOString().slice(0, 10);
}

exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  const apiKey = process.env.POLYGON_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'POLYGON_API_KEY not configured' }) };
  }

  let product;
  try {
    ({ product } = JSON.parse(event.body || '{}'));
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Invalid JSON body' }) };
  }

  if (!['ZQ', 'SR3'].includes(product)) {
    return { statusCode: 400, body: JSON.stringify({ error: 'product must be ZQ or SR3' }) };
  }

  const tickers = product === 'ZQ' ? zqTickers() : sr3Tickers();
  const tickerParam = tickers.join(',');
  const url = `${POLYGON_BASE}/v3/snapshot?ticker.any_of=${encodeURIComponent(tickerParam)}&apiKey=${apiKey}`;

  let polygonData;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const text = await res.text();
      return { statusCode: 502, body: JSON.stringify({ error: `Polygon ${res.status}`, detail: text }) };
    }
    polygonData = await res.json();
  } catch (e) {
    return { statusCode: 502, body: JSON.stringify({ error: 'Polygon fetch failed', detail: e.message }) };
  }

  const results = polygonData.results || [];

  // Build a lookup so we can return contracts in calendar order
  const byTicker = {};
  for (const r of results) {
    const settle = r.day?.close ?? r.lastTrade?.price ?? r.prevDay?.close;
    if (settle == null) continue;
    const implied_rate = +(100 - settle).toFixed(5);
    const expiry = r.details?.expiration_date ?? symbolToExpiry(r.ticker);
    byTicker[r.ticker] = { symbol: r.ticker, settle: +settle, implied_rate, expiry };
  }

  // Return in the same order as our generated ticker list (calendar order)
  const out = tickers
    .filter(t => byTicker[t])
    .map(t => byTicker[t]);

  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
    body: JSON.stringify(out),
  };
};
