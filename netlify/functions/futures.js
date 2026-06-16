// Massive.com futures proxy — server-side to keep API key out of the browser.
// Uses /futures/v1/aggs/{ticker} (included in Futures Basic free plan).
// POST { product: 'ZQ' | 'SR3' }  →  [{ symbol, settle, implied_rate, expiry }]

const BASE = 'https://api.massive.com';

const MC     = { 1:'F',2:'G',3:'H',4:'J',5:'K',6:'M',7:'N',8:'Q',9:'U',10:'V',11:'X',12:'Z' };
const MC_INV = Object.fromEntries(Object.entries(MC).map(([k,v])=>[v,+k]));

function addM(y, m, n) {
  m += n;
  while (m > 12) { m -= 12; y++; }
  return { y, m };
}
function dim(y, m) { return new Date(y, m, 0).getDate(); }

// 30-Day Fed Funds: monthly, current month + 17 more = 18 contracts.
// CME product code is "FF" (Globex symbol is "ZQ" but Massive uses product codes).
function zqTickers() {
  const now = new Date();
  let y = now.getFullYear(), m = now.getMonth() + 1;
  const out = [];
  for (let i = 0; i < 18; i++) {
    out.push(`FF${MC[m]}${y % 10}`);
    const n = addM(y, m, 1); y = n.y; m = n.m;
  }
  return out;
}

// SR3: quarterly IMM (Mar/Jun/Sep/Dec), next 8 contracts
function sr3Tickers() {
  const now = new Date();
  let y = now.getFullYear(), m = now.getMonth() + 1;
  while (![3,6,9,12].includes(m)) { const n = addM(y,m,1); y=n.y; m=n.m; }
  const out = [];
  for (let i = 0; i < 8; i++) {
    out.push(`SR3${MC[m]}${y % 10}`);
    let n = addM(y, m, 3); y = n.y; m = n.m;
    while (![3,6,9,12].includes(m)) { n = addM(y,m,1); y=n.y; m=n.m; }
  }
  return out;
}

// Derive month-end expiry from CME symbol (close enough for charting/sorting)
function symbolToExpiry(sym) {
  const mc     = sym[sym.length - 2];
  const yDigit = parseInt(sym[sym.length - 1], 10);
  const now    = new Date();
  const decade = Math.floor(now.getFullYear() / 10) * 10;
  const year   = yDigit + decade < now.getFullYear() - 1 ? yDigit + decade + 10 : yDigit + decade;
  const month  = MC_INV[mc];
  return new Date(year, month - 1, dim(year, month)).toISOString().slice(0, 10);
}

// Fetch the most recent daily close for one ticker via the aggs endpoint.
// Returns { settle, implied_rate } or null if no data.
async function fetchAgg(ticker, fromDate, toDate, apiKey) {
  const url = `${BASE}/futures/v1/aggs/${encodeURIComponent(ticker)}` +
    `?multiplier=1&timespan=day&from=${fromDate}&to=${toDate}` +
    `&sort=desc&limit=1&apiKey=${apiKey}`;
  const res = await fetch(url);
  if (!res.ok) {
    // 404 = contract not listed; skip silently
    if (res.status === 404) return null;
    throw new Error(`Massive ${res.status} for ${ticker}`);
  }
  const json = await res.json();
  const rows = json.results || json.result || [];
  if (!rows.length) return null;
  // Field names follow Polygon convention: c = close, v = volume
  const close = rows[0].c ?? rows[0].close ?? rows[0].settlement_price;
  if (close == null) return null;
  return { settle: +close, implied_rate: +(100 - close).toFixed(5) };
}

exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  const apiKey = process.env.MASSIVE_API_KEY || process.env.POLYGON_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'MASSIVE_API_KEY not configured' }) };
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

  // Look back up to 7 calendar days to catch weekends/holidays
  const now     = new Date();
  const toDate  = now.toISOString().slice(0, 10);
  const fromObj = new Date(now); fromObj.setDate(fromObj.getDate() - 14);
  const fromDate = fromObj.toISOString().slice(0, 10);

  // Fetch all tickers in parallel
  const settled = await Promise.allSettled(
    tickers.map(t => fetchAgg(t, fromDate, toDate, apiKey).then(data => ({ ticker: t, data })))
  );

  const out = [];
  for (let i = 0; i < tickers.length; i++) {
    const r = settled[i];
    if (r.status === 'rejected' || !r.value?.data) continue;
    const { ticker, data } = r.value;
    out.push({
      symbol:       ticker,
      settle:       data.settle,
      implied_rate: data.implied_rate,
      expiry:       symbolToExpiry(ticker),
    });
  }

  if (!out.length) {
    return {
      statusCode: 502,
      body: JSON.stringify({ error: 'No data returned from Massive for any contract' }),
    };
  }

  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
    body: JSON.stringify(out),
  };
};
