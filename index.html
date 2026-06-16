// netlify/functions/claude.js
// ─────────────────────────────────────────────────────────────────────────────
// Serverless proxy for the Anthropic API.
// The ANTHROPIC_API_KEY environment variable is set in Netlify's dashboard
// and never exposed to the browser — this function is the only code that
// touches it. The browser calls /.netlify/functions/claude instead of
// api.anthropic.com directly.
// ─────────────────────────────────────────────────────────────────────────────

exports.handler = async function (event) {

  // Only accept POST requests
  if (event.httpMethod !== 'POST') {
    return {
      statusCode: 405,
      body: JSON.stringify({ error: 'Method not allowed' }),
    };
  }

  // API key lives in Netlify environment variables — never in client code
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'API key not configured on server' }),
    };
  }

  // Parse the request body sent from the dashboard
  let payload;
  try {
    payload = JSON.parse(event.body);
  } catch {
    return {
      statusCode: 400,
      body: JSON.stringify({ error: 'Invalid JSON body' }),
    };
  }

  // Allowlist the fields we forward — never blindly proxy arbitrary requests
  const { model, max_tokens, system, messages } = payload;

  if (!messages || !Array.isArray(messages)) {
    return {
      statusCode: 400,
      body: JSON.stringify({ error: 'messages array required' }),
    };
  }

  // Forward to Anthropic
  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type':         'application/json',
        'x-api-key':            apiKey,
        'anthropic-version':    '2023-06-01',
      },
      body: JSON.stringify({
        model:      model      || 'claude-sonnet-4-6',
        max_tokens: max_tokens || 1000,
        system:     system     || '',
        messages,
      }),
    });

    const data = await response.json();

    // Surface Anthropic errors cleanly rather than returning garbled responses
    if (!response.ok) {
      return {
        statusCode: response.status,
        body: JSON.stringify({ error: data?.error?.message || 'Anthropic API error' }),
      };
    }

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    };

  } catch (err) {
    return {
      statusCode: 502,
      body: JSON.stringify({ error: 'Failed to reach Anthropic API', detail: err.message }),
    };
  }
};
