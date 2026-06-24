// Twitter/X feed proxy — SocialData.tools API.
// GET /.netlify/functions/tweets  →  [{ account, text, date, url }]

const ACCOUNTS = ['Globalflows', 'conksresearch'];
const BASE = 'https://api.socialdata.tools/twitter/search';

async function fetchAccount(account, apiKey) {
  const query = encodeURIComponent(`from:${account}`);
  const res = await fetch(`${BASE}?query=${query}&type=Latest`, {
    headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) {
    const txt = await res.text();
    console.error(`[tweets] ${account} → HTTP ${res.status}: ${txt.slice(0,200)}`);
    return [];
  }
  const data = await res.json();
  const tweets = data.tweets || data.data || (Array.isArray(data) ? data : []);
  return tweets.slice(0, 8).map(t => ({
    account,
    text: (t.full_text || t.text || '').trim(),
    url: t.id_str
      ? `https://x.com/${account}/status/${t.id_str}`
      : (t.url || ''),
    date: t.created_at || '',
  }));
}

exports.handler = async () => {
  const apiKey = process.env.SOCIALDATA_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'SOCIALDATA_API_KEY not configured' }) };
  }

  const results = await Promise.allSettled(ACCOUNTS.map(a => fetchAccount(a, apiKey)));
  const posts = results.flatMap(r => r.status === 'fulfilled' ? r.value : []);

  console.log(`[tweets] total posts: ${posts.length}`);

  posts
    .filter(p => p.text && !p.text.startsWith('RT @'))
    .sort((a, b) => {
      const da = a.date ? new Date(a.date) : 0;
      const db = b.date ? new Date(b.date) : 0;
      return db - da;
    });

  const filtered = posts
    .filter(p => p.text && !p.text.startsWith('RT @'))
    .sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0));

  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=300' },
    body: JSON.stringify(filtered.slice(0, 16)),
  };
};
