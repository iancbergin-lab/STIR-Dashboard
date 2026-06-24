// Twitter/X feed proxy — tries multiple Nitter instances in sequence.
// Nitter exposes RSS at /{username}/rss with no auth required.
// GET /.netlify/functions/tweets  →  [{ account, text, date, url }]

const ACCOUNTS = ['Globalflows', 'conksresearch'];

// Nitter instances known to be operational — tried in order, first success wins.
const NITTER_INSTANCES = [
  'https://nitter.poast.org',
  'https://nitter.privacydev.net',
  'https://nitter.1d4.us',
  'https://nitter.it',
  'https://nitter.nl',
];

function extractItems(xml, account) {
  const items = [];
  const itemRx = /<item>([\s\S]*?)<\/item>/g;
  let m;
  while ((m = itemRx.exec(xml)) !== null) {
    const block = m[1];
    const title   = (/<title><!\[CDATA\[([\s\S]*?)\]\]><\/title>/.exec(block) || /<title>([\s\S]*?)<\/title>/.exec(block) || [])[1] || '';
    const pubDate  = (/<pubDate>([\s\S]*?)<\/pubDate>/.exec(block) || [])[1] || '';
    const desc     = (/<description><!\[CDATA\[([\s\S]*?)\]\]><\/description>/.exec(block) || /<description>([\s\S]*?)<\/description>/.exec(block) || [])[1] || '';
    // Nitter puts tweet text in description (HTML); title is truncated
    const raw = desc.length > title.length ? desc : title;
    const text = raw
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<[^>]+>/g, '')
      .replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>')
      .replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&nbsp;/g,' ')
      .replace(/\n{3,}/g, '\n\n').trim();
    // Nitter links point to the nitter instance — rewrite to x.com
    const nitterLink = (/<link>([\s\S]*?)<\/link>/.exec(block) || [])[1] || '';
    const url = nitterLink.trim().replace(/^https?:\/\/[^/]+/, 'https://x.com');
    if (text && !text.startsWith('RT by')) {   // skip retweet-of-retweet noise
      items.push({ text, url, date: pubDate.trim() });
    }
  }
  return items;
}

async function fetchFeed(account) {
  for (const instance of NITTER_INSTANCES) {
    try {
      const res = await fetch(`${instance}/${account}/rss`, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; STIR-Dashboard/1.0)',
          'Accept': 'application/rss+xml, application/xml, text/xml',
        },
        signal: AbortSignal.timeout(7000),
      });
      if (!res.ok) continue;
      const xml = await res.text();
      // Quick sanity check — valid Nitter RSS contains <channel>
      if (!xml.includes('<channel>')) continue;
      const items = extractItems(xml, account);
      if (items.length) {
        console.log(`[tweets] ${account}: ${items.length} posts from ${instance}`);
        return items.slice(0, 8).map(i => ({ account, ...i }));
      }
    } catch (e) {
      console.warn(`[tweets] ${instance} failed for ${account}: ${e.message}`);
    }
  }
  console.error(`[tweets] all instances failed for ${account}`);
  return [];
}

exports.handler = async () => {
  const results = await Promise.allSettled(ACCOUNTS.map(fetchFeed));
  const posts = results.flatMap(r => r.status === 'fulfilled' ? r.value : []);

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
    body: JSON.stringify(posts),
  };
};
