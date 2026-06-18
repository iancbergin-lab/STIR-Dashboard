// Anthropic API proxy — keeps API key out of the browser.
// POST { messages: [{role, content}], system: string }
// → { text: string }

exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return { statusCode: 500, body: JSON.stringify({ error: 'ANTHROPIC_API_KEY not configured' }) };
  }

  let messages, system;
  try {
    ({ messages, system } = JSON.parse(event.body || '{}'));
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Invalid JSON body' }) };
  }

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1000,
      system,
      messages,
    }),
  });

  const data = await res.json();
  if (!res.ok) {
    return { statusCode: res.status, body: JSON.stringify({ error: data.error?.message || 'Anthropic error' }) };
  }

  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: data.content?.[0]?.text ?? '' }),
  };
};
