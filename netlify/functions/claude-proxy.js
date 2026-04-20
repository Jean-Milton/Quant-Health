// Netlify Function: claude-proxy.js
// Proxies Claude API calls server-side so the browser never hits api.anthropic.com directly.
// This avoids CORS errors when the app is served from Netlify.
//
// No environment variables needed — the API key is injected automatically
// by Netlify's Claude integration when the app is deployed on Claude.ai infrastructure,
// or you can add ANTHROPIC_API_KEY as an environment variable in Netlify dashboard.

exports.handler = async function(event) {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Content-Type': 'application/json'
  };

  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 200, headers, body: '' };
  }

  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  let body;
  try {
    body = JSON.parse(event.body);
  } catch(e) {
    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Invalid JSON' }) };
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return {
      statusCode: 500, headers,
      body: JSON.stringify({ error: 'ANTHROPIC_API_KEY not configured in Netlify environment variables.' })
    };
  }

  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify(body)
    });

    const data = await response.json();
    return { statusCode: response.status, headers, body: JSON.stringify(data) };

  } catch(e) {
    return {
      statusCode: 500, headers,
      body: JSON.stringify({ error: 'Proxy error', message: e.message })
    };
  }
};
