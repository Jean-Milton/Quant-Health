# QuantHealth — Local Data Scripts

Local Python scripts for importing Garmin Connect and Strava data into the QuantHealth web app.

These scripts run on your personal computer, parse or fetch data from each service, and write a JSON file that you transfer to your phone and import into QuantHealth via **Sync → Restore / Import**. No cloud, no accounts beyond your existing Garmin and Strava logins, no data leaves your control.

---

## Scripts

| Script | Purpose | When to run |
|---|---|---|
| `garmin-inspect.py` | Scans a Garmin export folder and reports what data is actually present. Used once for schema discovery. | Once, after first Garmin export |
| `garmin-import.py` | Parses an unzipped Garmin Connect data export into QuantHealth-compatible JSON. | Once per Garmin export (quarterly or as needed) |
| `strava-export.py` | Pulls recent Strava activities and computes CTL/ATL/TSB fitness metrics. | Weekly, automated or manual |

---

## Prerequisites

- **Python 3.10 or newer** — https://python.org/downloads
  During install, check **"Add Python to PATH"**.

- **Dependencies** for `strava-export.py` only:
  ```
  pip install -r requirements.txt
  ```
  The Garmin scripts have no external dependencies — pure standard library.

---

## Garmin workflow

Garmin does not expose an open API. The path is to use Garmin Connect's official data export (free, one-shot per request).

### Request your data

1. Visit [garmin.com](https://www.garmin.com) → Account icon → **Account Information** → **Export Your Data** → **Request My Data**
2. Wait 24–48 hours for Garmin's email
3. Download the ZIP (the link expires in 14 days)
4. Unzip to a folder on your PC

### Parse the export

```
python garmin-import.py "C:\path\to\unzipped\garmin\export"
```

The script walks the export structure and extracts:

| Source folder | Data |
|---|---|
| `DI-Connect-Wellness/*sleepData*.json` | Sleep duration, deep/light/awake minutes |
| `DI-Connect-Aggregator/UDSFile_*.json` | Daily steps, resting HR, stress, intensity minutes, active calories, floors |
| `DI-Connect-Metrics/MetricsMaxMetData*.json` | VO2 Max history |

Output is written to `~/Documents/QuantHealth/garmin-export-YYYY-MM-DD.json` by default.

### Import into QuantHealth

Transfer the JSON to your phone (iCloud, OneDrive, email, etc.), open QuantHealth → **Sync → Restore / Import** → select the file. The app uses field-level merge — existing manual entries are preserved.

### Optional — inspect first

If you want to verify what's in a new export before importing:

```
python garmin-inspect.py "C:\path\to\unzipped\garmin\export"
```

Prints a summary of every field found and flags items of interest. Useful if Garmin changes their export format.

---

## Strava workflow

Strava has a real OAuth API — ongoing sync is possible without manual steps per run.

### One-time setup

1. **Register a personal API application** at https://www.strava.com/settings/api
   - Application Name: *QuantHealth Personal*
   - Category: *Visualizer* (does not affect functionality)
   - Website: `http://localhost`
   - Authorization Callback Domain: `localhost`
   - Upload any small image for the icon

2. **Save your credentials** to `~/.quanthealth/.env` (Windows: `%USERPROFILE%\.quanthealth\.env`):
   ```
   STRAVA_CLIENT_ID=12345
   STRAVA_CLIENT_SECRET=abcdef0123456789...
   ```

3. **First run — browser authorization:**
   ```
   python strava-export.py
   ```
   A browser opens to Strava. Click *Authorize*. The script catches the callback and stores a refresh token. You will not need to authorize again for months.

### Ongoing runs

```
python strava-export.py
```

By default pulls the last 30 days of activities and writes to `~/Documents/QuantHealth/strava-export-YYYY-MM-DD.json`.

### Options

| Flag | Default | Purpose |
|---|---|---|
| `--days N` | 30 | Days of history to pull. Strava recommends ≤30 per sync. |
| `--output PATH` | `~/Documents/QuantHealth` | Output folder |
| `--lthr N` | 144 | Lactate threshold HR for TSS calculation |
| `--reauth` | off | Force fresh authorization |

### Computed metrics

Strava's public API does not expose CTL (fitness), ATL (fatigue), or TSB (form). These are computed locally using the standard TrainingPeaks exponentially-weighted moving average formula over HR-based TSS. Absolute values will differ slightly from Strava's web UI; trends will align.

### Automated scheduling (optional)

**Windows** — via Task Scheduler:

1. Create Basic Task → *QuantHealth Strava Export*
2. Trigger: Weekly, Sunday 6:00 AM
3. Action: Start a program
   - Program: `python`
   - Arguments: `C:\path\to\quanthealth\scripts\strava-export.py`

If your output folder is inside OneDrive, the JSON appears on your phone automatically.

**macOS / Linux** — via cron:

```
0 6 * * 0 /usr/bin/python3 /path/to/scripts/strava-export.py
```

---

## Configuration files

These live outside the repo, in your user home directory:

```
~/.quanthealth/
├── .env                    Strava API credentials
└── strava-token.json       OAuth refresh token (auto-managed)
```

Never commit these to the repo — they are personal secrets.

---

## Troubleshooting

**`'python' is not recognized`** — Python not installed, or "Add to PATH" was skipped during install. Reinstall from python.org.

**`ModuleNotFoundError: No module named 'requests'`** — Run `pip install -r requirements.txt` from the `scripts/` folder.

**Strava OAuth — browser won't redirect** — Check that port 8754 is not in use. Run with `--reauth` to force fresh authorization.

**Strava `429 Too Many Requests`** — Rate limit (200 requests / 15 minutes). Wait 15 minutes and retry.

**Garmin `ERROR: No DI_CONNECT folder`** — The path passed is not the export root. Pass the folder that directly contains `DI_CONNECT`, not a parent folder.

**CTL/ATL/TSB differ from Strava's web UI** — Expected. Strava uses proprietary formulas. The script uses the TrainingPeaks standard (42-day CTL, 7-day ATL, HR-based TSS).

---

## Data privacy

All scripts operate locally:

- Garmin scripts read files from disk; they never connect to any network.
- The Strava script connects only to `strava.com` for OAuth and data fetch.
- No data is sent to third parties.
- Credentials are stored in `~/.quanthealth/` with restrictive file permissions (best-effort on Windows, `chmod 600` on Unix).

The QuantHealth web app itself runs entirely in your browser's local storage. Nothing leaves your device unless you export it yourself.

---

## Repository layout

```
Quant-Health/
├── index.html                    QuantHealth PWA (deployed via Cloudflare Pages)
└── scripts/                      Local-run scripts (this folder)
    ├── README.md                 This file
    ├── requirements.txt          Python dependencies for strava-export.py
    ├── garmin-inspect.py         Garmin export structure inspector
    ├── garmin-import.py          Garmin export parser
    └── strava-export.py          Strava API export + TSS/CTL/ATL/TSB computation
```
