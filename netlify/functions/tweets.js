// Twitter/X feed proxy — Apify tweet-scraper (async mode).
// First call triggers a new run and returns whatever the last run produced.
// Subsequent calls within 5 min return cached data from the last run.
// GET /.netlify/functions/tweets  →  [{ account, text, date, url }]

const ACCOUNTS = ['Globalflows', 'conksresearch'];
const ACTOR = 'apidojo~tweet-scraper';
const BASE  = 'https://api.apify.com/v2';

exports.handler = async () => {
  const apiKey = process.env.APIFY_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'APIFY_API_KEY not configured' }) };
  }

  // 1. Kick off a new async run (fire-and-forget — don't wait for it)
  fetch(`${BASE}/acts/${ACTOR}/runs?token=${apiKey}&memory=256`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      startUrls: ACCOUNTS.map(a => `https://x.com/${a}`),
      maxItems: 20,
      addUserInfo: false,
    }),
  }).catch(e => console.warn('[tweets] run trigger failed:', e.message));

  // 2. Read dataset from the LAST SUCCEEDED run — usually instant
  let items = [];
  try {
    const res = await fetch(
      `${BASE}/acts/${ACTOR}/runs/last/dataset/items?token=${apiKey}&status=SUCCEEDED&limit=20`,
      { signal: AbortSignal.timeout(8000) }
    );
    console.log('[tweets] dataset fetch status:', res.status);
    if (res.ok) {
      const data = await res.json();
      console.log('[tweets] raw items from last run:', Array.isArray(data) ? data.length : typeof data);
      items = Array.isArray(data) ? data : [];
    } else {
      const txt = await res.text();
      console.error('[tweets] dataset error:', txt.slice(0, 300));
    }
  } catch (e) {
    console.error('[tweets] fetch error:', e.message);
  }

  const posts = items
    .filter(t => t.fullText || t.text)
    .map(t => {
      const account = (t.author?.userName || t.authorId || '').replace(/^@/, '');
      const text = (t.fullText || t.text || '').trim();
      const url = t.url || (t.id ? `https://x.com/${account}/status/${t.id}` : '');
      const date = t.createdAt || '';
      return { account, text, url, date };
    })
    .filter(p => p.text && !p.text.startsWith('RT @'));

  posts.sort((a, b) => {
    const da = a.date ? new Date(a.date) : 0;
    const db = b.date ? new Date(b.date) : 0;
    return db - da;
  });

  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=300',
    },
    body: JSON.stringify(posts.slice(0, 16)),
  };
};
