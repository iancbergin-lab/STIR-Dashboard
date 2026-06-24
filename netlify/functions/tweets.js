// Twitter/X feed proxy via RSSHub public instance.
// Falls back to a second nitter mirror if the first fails.
// GET /.netlify/functions/tweets  →  [{ account, text, date, url }]

const ACCOUNTS = ['Globalflows', 'conksresearch'];

// Primary: RSSHub public instance (well-maintained, supports Twitter)
// Fallback: alternative RSSHub self-hosted mirror
const RSS_BASES = [
  'https://rsshub.app/twitter/user',
  'https://rsshub.vercel.app/twitter/user',
];

function extractItems(xml) {
  const items = [];
  const itemRx = /<item>([\s\S]*?)<\/item>/g;
  let m;
  while ((m = itemRx.exec(xml)) !== null) {
    const block = m[1];
    const title  = (/<title><!\[CDATA\[([\s\S]*?)\]\]><\/title>/.exec(block) || /<title>([\s\S]*?)<\/title>/.exec(block) || [])[1] || '';
    const link   = (/<link>([\s\S]*?)<\/link>/.exec(block) || /<link\s+href="([^"]+)"/.exec(block) || [])[1] || '';
    const pubDate= (/<pubDate>([\s\S]*?)<\/pubDate>/.exec(block) || [])[1] || '';
    const desc   = (/<description><!\[CDATA\[([\s\S]*?)\]\]><\/description>/.exec(block) || /<description>([\s\S]*?)<\/description>/.exec(block) || [])[1] || '';
    // Use description if richer, else title
    const raw = desc.length > title.length ? desc : title;
    // Strip HTML tags
    const text = raw.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").trim();
    if (text) items.push({ text, url: link.trim(), date: pubDate.trim() });
  }
  return items;
}

async function fetchFeed(account) {
  for (const base of RSS_BASES) {
    try {
      const res = await fetch(`${base}/${account}`, {
        headers: { 'User-Agent': 'STIR-Dashboard/1.0' },
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) continue;
      const xml = await res.text();
      const items = extractItems(xml);
      if (items.length) return items.slice(0, 10).map(i => ({ account, ...i }));
    } catch {
      // try next base
    }
  }
  return [];
}

exports.handler = async () => {
  const results = await Promise.allSettled(ACCOUNTS.map(fetchFeed));
  const posts = results.flatMap(r => r.status === 'fulfilled' ? r.value : []);

  // Sort newest first (best-effort — pubDate format varies)
  posts.sort((a, b) => {
    const da = a.date ? new Date(a.date) : 0;
    const db = b.date ? new Date(b.date) : 0;
    return db - da;
  });

  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=300', // cache 5 min at CDN edge
    },
    body: JSON.stringify(posts),
  };
};
