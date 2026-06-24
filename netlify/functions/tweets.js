// Twitter/X feed proxy — uses Apify's tweet-scraper actor.
// GET /.netlify/functions/tweets  →  [{ account, text, date, url }]

const ACCOUNTS = ['Globalflows', 'conksresearch'];

exports.handler = async () => {
  const apiKey = process.env.APIFY_API_KEY;
  if (!apiKey) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'APIFY_API_KEY not configured' }),
    };
  }

  const startUrls = ACCOUNTS.map(a => `https://x.com/${a}`);

  // Run actor synchronously and return dataset items in one call.
  // memory=256 keeps it on the free tier; timeout=45s is generous for 2 accounts.
  const runUrl =
    `https://api.apify.com/v2/acts/apidojo~tweet-scraper/run-sync-get-dataset-items` +
    `?token=${apiKey}&timeout=45&memory=256`;

  let items;
  try {
    const res = await fetch(runUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        startUrls,
        maxItems: 20,
        addUserInfo: false,
      }),
      signal: AbortSignal.timeout(50000),
    });
    if (!res.ok) {
      const txt = await res.text();
      console.error(`[tweets] Apify error ${res.status}: ${txt}`);
      return { statusCode: 502, body: JSON.stringify({ error: `Apify ${res.status}` }) };
    }
    items = await res.json();
  } catch (e) {
    console.error(`[tweets] fetch failed: ${e.message}`);
    return { statusCode: 502, body: JSON.stringify({ error: e.message }) };
  }

  if (!Array.isArray(items)) {
    console.error(`[tweets] unexpected Apify response: ${JSON.stringify(items).slice(0,300)}`);
    items = [];
  }
  console.log(`[tweets] Apify returned ${items.length} raw items`);

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
