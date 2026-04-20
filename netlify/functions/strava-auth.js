// Netlify Function: strava-auth.js
// Handles Strava OAuth token exchange and refresh securely server-side.
// Client Secret never exposed to the browser.
//
// Environment variables required (set in Netlify dashboard):
//   STRAVA_CLIENT_ID     — from strava.com/settings/api
//   STRAVA_CLIENT_SECRET — from strava.com/settings/api

exports.handler = async function(event) {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Content-Type': 'application/json'
  };

  // Handle CORS preflight
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

  const { action, code, refresh_token } = body;
  const CLIENT_ID     = process.env.STRAVA_CLIENT_ID;
  const CLIENT_SECRET = process.env.STRAVA_CLIENT_SECRET;

  if (!CLIENT_ID || !CLIENT_SECRET) {
    return {
      statusCode: 500, headers,
      body: JSON.stringify({ error: 'Strava credentials not configured. Add STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in Netlify environment variables.' })
    };
  }

  try {
    let stravaBody;

    if (action === 'exchange' && code) {
      // Initial OAuth code exchange
      stravaBody = new URLSearchParams({
        client_id:     CLIENT_ID,
        client_secret: CLIENT_SECRET,
        code:          code,
        grant_type:    'authorization_code'
      });
    } else if (action === 'refresh' && refresh_token) {
      // Refresh expired access token
      stravaBody = new URLSearchParams({
        client_id:     CLIENT_ID,
        client_secret: CLIENT_SECRET,
        refresh_token: refresh_token,
        grant_type:    'refresh_token'
      });
    } else {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Invalid action. Use exchange or refresh.' }) };
    }

    const response = await fetch('https://www.strava.com/oauth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: stravaBody.toString()
    });

    const data = await response.json();

    if (!response.ok) {
      return {
        statusCode: response.status, headers,
        body: JSON.stringify({ error: data.message || 'Strava API error', details: data })
      };
    }

    // Return only what the client needs — never echo back the secret
    return {
      statusCode: 200, headers,
      body: JSON.stringify({
        access_token:  data.access_token,
        refresh_token: data.refresh_token,
        expires_at:    data.expires_at,
        athlete: data.athlete ? {
          id:         data.athlete.id,
          firstname:  data.athlete.firstname,
          lastname:   data.athlete.lastname,
          profile:    data.athlete.profile_medium
        } : null
      })
    };

  } catch(e) {
    return {
      statusCode: 500, headers,
      body: JSON.stringify({ error: 'Function error', message: e.message })
    };
  }
};
