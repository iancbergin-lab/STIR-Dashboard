// Twitter/X feed proxy — Apify twitter-user-scraper actor.
// GET /.netlify/functions/tweets  →  [{ account, text, date, url }]

const ACCOUNTS = ['Globalflows', 'conksresearch'];
const ACTOR = 'apidojo~twitter-user-scraper';
const BASE  = 'https://api.apify.com/v2';

exports.handler = async () => {
  const apiKey = process.env.APIFY_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'APIFY_API_KEY not configured' }) };
  }

  // Fire-and-forget: trigger a fresh run for next time
  fetch(`${BASE}/acts/${ACTOR}/runs?token=${apiKey}&memory=256`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      usernames: ACCOUNTS,
      tweetsDesired: 10,
    }),
  }).catch(e => console.warn('[tweets] run trigger failed:', e.message));

  // Read dataset from last succeeded run
  let items = [];
  try {
    const res = await fetch(
      `${BASE}/acts/${ACTOR}/runs/last/dataset/items?token=${apiKey}&status=SUCCEEDED&limit=30`,
      { signal: AbortSignal.timeout(8000) }
    );
    console.log('[tweets] dataset status:', res.status);
    if (res.ok) {
      const data = await res.json();
      console.log('[tweets] items:', Array.isArray(data) ? data.length : typeof data);
      if (Array.isArray(data) && data.length) {
        console.log('[tweets] keys:', Object.keys(data[0]).join(', '));
        console.log('[tweets] sample:', JSON.stringify(data[0]).slice(0, 400));
      }
      items = Array.isArray(data) ? data : [];
    }
  } catch (e) {
    console.error('[tweets] error:', e.message);
  }

  const posts = items
    .filter(t => !t.noResults)
    .map(t => {
      // Try all known field name variants across Apify actors
      const text = (t.fullText || t.full_text || t.text || t.body || '').trim();
      const account = (
        t.author?.userName || t.author?.username ||
        t.user?.screen_name || t.username || t.authorName || ''
      ).replace(/^@/, '');
      const url = t.url || t.tweetUrl || t.tweet_url ||
        (t.id || t.id_str ? `https://x.com/${account}/status/${t.id || t.id_str}` : '');
      const date = t.createdAt || t.created_at || t.date || '';
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
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=300' },
    body: JSON.stringify(posts.slice(0, 16)),
  };
};
