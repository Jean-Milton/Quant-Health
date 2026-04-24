"""
QuantHealth — Strava Export Script
====================================
Pulls your Strava activities and computes CTL (fitness), ATL (fatigue),
TSB (form) from estimated Training Stress Score. Writes a JSON file
matching QuantHealth v2.8's import schema.

Setup: see scripts/README.md for one-time Strava API app registration.
Usage:  python strava-export.py
        python strava-export.py --days 30 --output C:\\Users\\you\\OneDrive\\QuantHealth
"""

import argparse
import json
import math
import os
import sys
import time
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import requests
except ImportError:
    print("ERROR: requests library not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print("ERROR: python-dotenv not installed. Run: pip install -r requirements.txt")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────
# CONFIG — edit if needed
# ─────────────────────────────────────────────────────────────────────
# Heart rate thresholds for TSS estimation. Tune these after a tested LTHR.
LTHR = 144  # Lactate threshold HR (your estimate based on age 51)
HR_MAX = 169  # Max HR estimate

# CTL/ATL/TSB exponential moving average constants
# Standard TrainingPeaks values: CTL = 42-day, ATL = 7-day
CTL_DAYS = 42
ATL_DAYS = 7

# Default config locations
DEFAULT_CONFIG_DIR = Path.home() / ".quanthealth"
DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "QuantHealth"
TOKEN_FILE = DEFAULT_CONFIG_DIR / "strava-token.json"
ENV_FILE = DEFAULT_CONFIG_DIR / ".env"

# Strava OAuth URLs
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
ATHLETE_URL = "https://www.strava.com/api/v3/athlete"

OAUTH_PORT = 8754  # Local port for OAuth callback
OAUTH_SCOPE = "read,activity:read_all,profile:read_all"

# Strava activity type → QuantHealth canonical type
TYPE_MAP = {
    "Ride": "MTB",                # We'll refine below using gear/sport_type
    "MountainBikeRide": "MTB",
    "VirtualRide": "Rollers",
    "GravelRide": "Ride",
    "EBikeRide": "Ride",
    "Run": "Run",
    "TrailRun": "Run",
    "VirtualRun": "Run",
    "Walk": "Walk",
    "Hike": "Hike",
    "WeightTraining": "Strength",
    "Workout": "Strength",
    "Yoga": "Recovery",
    "Crossfit": "Strength",
    "RockClimbing": "Strength",
    "Swim": "Swim",
}


# ─────────────────────────────────────────────────────────────────────
# OAUTH FLOW — one-time browser-based authorization
# ─────────────────────────────────────────────────────────────────────
class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Catches the OAuth callback from Strava and extracts the auth code."""
    auth_code = None
    error = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;padding:40px;background:#0b0f14;color:#dde4ec">
                <h1 style="color:#00c8ff">QuantHealth</h1>
                <p>Strava authorization successful. You can close this tab.</p>
                </body></html>""")
        else:
            OAuthCallbackHandler.error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization failed.</h1>")

    def log_message(self, *args):
        pass  # Silence default HTTP server logging


def run_oauth_flow(client_id: str, client_secret: str) -> dict:
    """Browser-based OAuth flow. Returns a token dict."""
    redirect_uri = f"http://localhost:{OAUTH_PORT}/callback"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "approval_prompt": "auto",
        "scope": OAUTH_SCOPE,
    }
    auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"

    print("\n  Opening Strava authorization in your browser...")
    print(f"  If it doesn't open, paste this URL manually:\n    {auth_url}\n")
    webbrowser.open(auth_url)

    # Spin up a tiny local server to catch the callback
    server = HTTPServer(("localhost", OAUTH_PORT), OAuthCallbackHandler)
    server.timeout = 120
    print(f"  Waiting for authorization (timeout 2 min)...")
    server.handle_request()

    if OAuthCallbackHandler.error:
        raise RuntimeError(f"Strava OAuth error: {OAuthCallbackHandler.error}")
    if not OAuthCallbackHandler.auth_code:
        raise RuntimeError("No authorization code received (timed out?)")

    print("  ✓ Authorization received, exchanging for token...")
    resp = requests.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": OAuthCallbackHandler.auth_code,
        "grant_type": "authorization_code",
    }, timeout=30)
    resp.raise_for_status()
    token = resp.json()
    save_token(token)
    print("  ✓ Token saved.")
    return token


def save_token(token: dict):
    DEFAULT_CONFIG_DIR.mkdir(exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    # Best-effort permission lockdown (Windows: limited effect, Unix: chmod 600)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except (OSError, NotImplementedError):
        pass


def load_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)


def refresh_if_needed(token: dict, client_id: str, client_secret: str) -> dict:
    """Refresh the access token if expired or expiring within 5 min."""
    now = int(time.time())
    if token.get("expires_at", 0) > now + 300:
        return token  # Still valid

    print("  Refreshing access token...")
    resp = requests.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    new_token = resp.json()
    save_token(new_token)
    return new_token


# ─────────────────────────────────────────────────────────────────────
# DATA PULL
# ─────────────────────────────────────────────────────────────────────
def fetch_activities(access_token: str, days: int = 30) -> list:
    """Pull activities from the last N days. Strava recommends ≤30 days/sync."""
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []
    page = 1
    while True:
        resp = requests.get(ACTIVITIES_URL, headers=headers,
                            params={"after": after, "page": page, "per_page": 200},
                            timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < 200:
            break
        page += 1
        # Be polite — respect 200 req / 15 min
        time.sleep(1)
    return activities


def fetch_athlete(access_token: str) -> dict:
    """Fetch athlete profile (currently unused beyond logging, but useful)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(ATHLETE_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────────────
# TSS / CTL / ATL / TSB CALCULATION
# ─────────────────────────────────────────────────────────────────────
def hr_tss(duration_sec: float, avg_hr: float | None, lthr: float = LTHR) -> float | None:
    """
    Estimate TSS from average HR using the heart-rate-based intensity factor.
    Formula:  IF = avgHR / LTHR
              TSS = (duration_hours * IF^2) * 100
    Returns None if no HR data.
    """
    if not avg_hr or avg_hr < 30:
        return None
    intensity_factor = avg_hr / lthr
    duration_hours = duration_sec / 3600.0
    tss = (duration_hours * intensity_factor ** 2) * 100
    return round(tss, 1)


def fallback_tss(duration_sec: float, sport_type: str) -> float:
    """When no HR is recorded, use a coarse duration-based estimate by sport."""
    duration_hours = duration_sec / 3600.0
    # Rough TSS/hr estimates for moderate effort
    rates = {
        "MTB": 60, "Ride": 55, "Rollers": 50, "Run": 70, "TrailRun": 75,
        "Walk": 25, "Hike": 35, "Strength": 35, "Recovery": 15, "Swim": 50,
    }
    return round(duration_hours * rates.get(sport_type, 40), 1)


def compute_ctl_atl_tsb(daily_tss: dict[str, float]) -> dict[str, dict]:
    """
    Compute CTL (42-day EMA), ATL (7-day EMA), TSB = CTL - ATL for each date.
    Uses the standard TrainingPeaks exponential moving average formula:
      CTL_today = CTL_yesterday + (TSS_today - CTL_yesterday) * (1 - exp(-1/42))
      ATL_today = ATL_yesterday + (TSS_today - ATL_yesterday) * (1 - exp(-1/7))
    """
    if not daily_tss:
        return {}

    # Get full date range from earliest to today
    dates_sorted = sorted(daily_tss.keys())
    start_date = datetime.strptime(dates_sorted[0], "%Y-%m-%d").date()
    end_date = datetime.now(timezone.utc).date()

    ctl_decay = 1 - math.exp(-1.0 / CTL_DAYS)
    atl_decay = 1 - math.exp(-1.0 / ATL_DAYS)

    ctl, atl = 0.0, 0.0
    output = {}
    cur = start_date
    while cur <= end_date:
        date_str = cur.isoformat()
        tss_today = daily_tss.get(date_str, 0.0)
        ctl = ctl + (tss_today - ctl) * ctl_decay
        atl = atl + (tss_today - atl) * atl_decay
        tsb = ctl - atl
        output[date_str] = {
            "stravaFitness": round(ctl, 1),
            "stravaFatigue": round(atl, 1),
            "strava": round(tsb, 1),
        }
        cur += timedelta(days=1)
    return output


# ─────────────────────────────────────────────────────────────────────
# QUANTHEALTH SCHEMA TRANSFORM
# ─────────────────────────────────────────────────────────────────────
def map_activity(act: dict) -> dict:
    """Convert a Strava activity to QuantHealth's activity shape."""
    sport_type = act.get("sport_type") or act.get("type", "")
    qh_type = TYPE_MAP.get(sport_type, sport_type)

    duration_min = round(act.get("moving_time", 0) / 60)
    avg_hr = act.get("average_heartrate")

    return {
        "id": f"strava-{act['id']}",
        "stravaId": act["id"],
        "type": qh_type,
        "name": act.get("name", "")[:80],
        "duration": duration_min,
        "avgHR": round(avg_hr) if avg_hr else None,
        "distance": round(act.get("distance", 0) / 1000, 2),  # km
        "elevation": round(act.get("total_elevation_gain", 0)),  # m
        "sufferScore": act.get("suffer_score"),
        "startTime": act.get("start_date_local"),
        "source": "strava",
    }


def build_export(activities: list, athlete: dict, lthr: float) -> dict:
    """Group activities by local date, compute fitness metrics, build export JSON."""
    # Group activities by local date (already in athlete's timezone)
    by_date: dict[str, list] = {}
    daily_tss: dict[str, float] = {}

    for act in activities:
        start_local = act.get("start_date_local", "")
        if not start_local:
            continue
        date = start_local[:10]  # YYYY-MM-DD
        by_date.setdefault(date, []).append(act)

        # Compute TSS for this activity
        avg_hr = act.get("average_heartrate")
        duration_sec = act.get("moving_time", 0)
        sport_type = act.get("sport_type") or act.get("type", "")
        qh_type = TYPE_MAP.get(sport_type, sport_type)

        tss = hr_tss(duration_sec, avg_hr, lthr)
        if tss is None:
            tss = fallback_tss(duration_sec, qh_type)
        daily_tss[date] = daily_tss.get(date, 0.0) + tss

    # Compute CTL/ATL/TSB across the full date range
    fitness_by_date = compute_ctl_atl_tsb(daily_tss)

    # Build entries — one per date that has activities OR fitness numbers
    entries = []
    all_dates = sorted(set(list(by_date.keys()) + list(fitness_by_date.keys())))
    for date in all_dates:
        entry = {"date": date}
        if date in by_date:
            entry["activities"] = [map_activity(a) for a in by_date[date]]
        if date in fitness_by_date:
            entry.update(fitness_by_date[date])
        entries.append(entry)

    return {
        "source": "strava",
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "schemaVersion": 1,
        "athlete": {
            "name": f"{athlete.get('firstname','')} {athlete.get('lastname','')}".strip(),
            "id": athlete.get("id"),
        },
        "config": {
            "lthr": lthr,
            "hrMax": HR_MAX,
            "ctlDays": CTL_DAYS,
            "atlDays": ATL_DAYS,
        },
        "entries": entries,
    }


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="QuantHealth Strava Export")
    parser.add_argument("--days", type=int, default=30,
                        help="Days of activity history to pull (default 30, Strava recommends max 30 per sync)")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT_DIR),
                        help="Output folder for the JSON export (default: ~/Documents/QuantHealth)")
    parser.add_argument("--lthr", type=int, default=LTHR,
                        help=f"Lactate threshold HR for TSS calc (default {LTHR})")
    parser.add_argument("--reauth", action="store_true",
                        help="Force re-authentication (ignore stored token)")
    args = parser.parse_args()

    # Load credentials
    if not ENV_FILE.exists():
        print(f"ERROR: No .env file at {ENV_FILE}")
        print("Create one with:")
        print("  STRAVA_CLIENT_ID=your_id_here")
        print("  STRAVA_CLIENT_SECRET=your_secret_here")
        print("See scripts/README.md for setup instructions.")
        sys.exit(1)

    load_dotenv(ENV_FILE)
    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(f"ERROR: STRAVA_CLIENT_ID or STRAVA_CLIENT_SECRET missing in {ENV_FILE}")
        sys.exit(1)

    print("┌" + "─" * 56 + "┐")
    print("│ QuantHealth — Strava Export                            │")
    print("└" + "─" * 56 + "┘")

    # Auth
    token = None if args.reauth else load_token()
    if token:
        try:
            token = refresh_if_needed(token, client_id, client_secret)
        except requests.HTTPError as e:
            print(f"  Token refresh failed ({e.response.status_code}). Re-authorizing...")
            token = None
    if not token:
        token = run_oauth_flow(client_id, client_secret)

    access_token = token["access_token"]

    # Pull data
    print(f"\n  Fetching last {args.days} days of activities...")
    try:
        activities = fetch_activities(access_token, args.days)
    except requests.HTTPError as e:
        print(f"  ✗ Strava API error: {e.response.status_code} — {e.response.text[:200]}")
        sys.exit(1)
    print(f"  ✓ Got {len(activities)} activities")

    print("  Fetching athlete profile...")
    athlete = fetch_athlete(access_token)
    print(f"  ✓ Athlete: {athlete.get('firstname')} {athlete.get('lastname')} (id {athlete.get('id')})")

    # Build export
    print(f"  Computing CTL/ATL/TSB (LTHR={args.lthr}, CTL={CTL_DAYS}d, ATL={ATL_DAYS}d)...")
    export = build_export(activities, athlete, args.lthr)
    n_entries = len(export["entries"])
    n_acts = sum(len(e.get("activities", [])) for e in export["entries"])
    print(f"  ✓ Built {n_entries} day-entries, {n_acts} activities")

    # Write
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_file = out_dir / f"strava-export-{today}.json"
    with open(out_file, "w") as f:
        json.dump(export, f, indent=2)

    size_kb = out_file.stat().st_size / 1024
    print(f"\n  ✓ Wrote {out_file} ({size_kb:.1f} KB)")
    print(f"\n  Next: open this file in QuantHealth → Sync → Restore/Import")
    if "OneDrive" in str(out_dir) or "iCloud" in str(out_dir):
        print(f"        (Should auto-sync to your phone via {out_dir.name})")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(130)
    except Exception as e:
        print(f"\n  ✗ Error: {e}")
        sys.exit(1)
