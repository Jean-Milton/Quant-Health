"""
QuantHealth — Garmin Export Import
===================================
Walks your unzipped Garmin Connect data export and produces a single JSON
file for import into QuantHealth (v2.8b+).

Data sources parsed:
  DI_CONNECT/DI-Connect-Wellness/*_sleepData.json
    → sleepHours, deepSleepMin, lightSleepMin, awakeSleepMin
  DI_CONNECT/DI-Connect-Aggregator/UDSFile_*.json
    → steps, restingHR, stressAvg, stressMax, stressAsleep,
      moderateMin, vigorousMin, activeKcal, floors, hrMin, hrMax
  DI_CONNECT/DI-Connect-Metrics/MetricsMaxMetData_*.json
    → vo2Max

Usage:
  python garmin-import.py "C:\\path\\to\\unzipped\\garmin\\export"
  python garmin-import.py . --output C:\\Users\\you\\OneDrive\\QuantHealth
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────
# SLEEP DATA PARSER
# ─────────────────────────────────────────────────────────────────────
def parse_sleep_files(wellness_dir: Path) -> dict[str, dict]:
    """
    Parse all *_sleepData.json files.
    Each file contains a list of sleep night objects, one per calendarDate.
    Returns: { "YYYY-MM-DD": {sleepHours, deepSleepMin, lightSleepMin, awakeSleepMin} }
    """
    out: dict[str, dict] = {}
    if not wellness_dir.exists():
        return out

    sleep_files = sorted(wellness_dir.glob("*sleepData*.json"))
    for f in sleep_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, list):
            continue

        for night in data:
            if not isinstance(night, dict):
                continue
            date = night.get("calendarDate")
            if not date:
                continue

            # Filter out nights where the watch wasn't worn
            # (UNCONFIRMED with no sleep fields = gap, OFF_WRIST = drawer)
            confirmation = night.get("sleepWindowConfirmationType", "")
            deep = night.get("deepSleepSeconds")
            light = night.get("lightSleepSeconds")
            awake = night.get("awakeSleepSeconds")

            # Must have at least one of the sleep fields to be useful
            if deep is None and light is None and awake is None:
                continue
            if confirmation == "OFF_WRIST":
                continue

            # Convert seconds → minutes (None → 0 for sum)
            d = (deep or 0) / 60.0
            l = (light or 0) / 60.0
            a = (awake or 0) / 60.0
            total_sleep_min = d + l  # Awake time excluded from total sleep
            total_in_bed_min = d + l + a

            if total_in_bed_min == 0:
                continue  # Still nothing meaningful

            out[date] = {
                "sleepHours": round(total_sleep_min / 60.0, 2),
                "deepSleepMin": round(d, 1),
                "lightSleepMin": round(l, 1),
                "awakeSleepMin": round(a, 1),
            }

    return out


# ─────────────────────────────────────────────────────────────────────
# AGGREGATOR DATA PARSER (the big one)
# ─────────────────────────────────────────────────────────────────────
def _extract_stress(all_day_stress: dict) -> dict:
    """
    Parse the allDayStress structure to pull out avg, max, and sleep-stress.
    Returns a dict of the fields we care about, or empty dict if unusable.
    """
    if not isinstance(all_day_stress, dict):
        return {}
    agg_list = all_day_stress.get("aggregatorList", [])
    if not isinstance(agg_list, list):
        return {}

    out = {}
    for agg in agg_list:
        if not isinstance(agg, dict):
            continue
        t = agg.get("type")
        avg = agg.get("averageStressLevel")
        # -2 means the watch wasn't worn; filter
        if avg is None or avg == -2:
            continue
        if t == "TOTAL":
            out["stressAvg"] = avg
            out["stressMax"] = agg.get("maxStressLevel")
        elif t == "ASLEEP":
            out["stressAsleep"] = avg
    return out


def parse_aggregator_files(agg_dir: Path) -> dict[str, dict]:
    """
    Parse all UDSFile_*.json files in the Aggregator folder.
    Each file has a list of per-day summary objects.
    Returns: { "YYYY-MM-DD": { steps, hr, stressAvg, ... } }
    """
    out: dict[str, dict] = {}
    if not agg_dir.exists():
        return out

    uds_files = sorted(agg_dir.glob("UDSFile_*.json"))
    for f in uds_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, list):
            continue

        for day in data:
            if not isinstance(day, dict):
                continue
            date = day.get("calendarDate")
            if not date:
                continue

            entry = {}

            # Steps — only real if > 0 AND wellness data was captured
            if day.get("includesWellnessData") and day.get("totalSteps") is not None:
                entry["steps"] = day["totalSteps"]

            # Resting HR — prefer currentDayRestingHeartRate over restingHeartRate
            # (the "current day" reading is more recent; restingHeartRate is 7-day avg)
            rhr = day.get("currentDayRestingHeartRate") or day.get("restingHeartRate")
            if rhr and rhr > 30:  # Sanity filter — V3 bad readings can show 0
                entry["hr"] = rhr

            # HR min/max for the day
            min_hr = day.get("minHeartRate")
            max_hr = day.get("maxHeartRate")
            if min_hr and min_hr > 30:
                entry["hrMin"] = min_hr
            if max_hr and max_hr > min_hr:
                entry["hrMax"] = max_hr

            # Intensity minutes
            if day.get("moderateIntensityMinutes") is not None:
                entry["moderateMin"] = day["moderateIntensityMinutes"]
            if day.get("vigorousIntensityMinutes") is not None:
                entry["vigorousMin"] = day["vigorousIntensityMinutes"]

            # Calories (active)
            if day.get("activeKilocalories") is not None:
                entry["activeKcal"] = round(day["activeKilocalories"])

            # Floors climbed (meters → approximate floors: 3m per floor)
            floors_m = day.get("floorsAscendedInMeters")
            if floors_m is not None:
                entry["floors"] = round(floors_m / 3.0, 1)

            # Stress
            stress = _extract_stress(day.get("allDayStress"))
            entry.update(stress)

            # Only keep if we got at least one useful field
            if entry:
                # Merge with any previous day entry (UDS files may overlap)
                if date in out:
                    # Prefer non-null values from either source
                    for k, v in entry.items():
                        if v is not None:
                            out[date][k] = v
                else:
                    out[date] = entry

    return out


# ─────────────────────────────────────────────────────────────────────
# VO2 MAX PARSER
# ─────────────────────────────────────────────────────────────────────
def parse_vo2_file(metrics_dir: Path) -> dict[str, dict]:
    """
    Parse MetricsMaxMetData_*.json — series of VO2 Max measurements over time.
    Each entry has a calendarDate and vo2MaxValue.
    Returns: { "YYYY-MM-DD": {vo2Max: float} }
    """
    out: dict[str, dict] = {}
    if not metrics_dir.exists():
        return out

    vo2_files = sorted(metrics_dir.glob("MetricsMaxMetData*.json"))
    for f in vo2_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, list):
            continue

        for meas in data:
            if not isinstance(meas, dict):
                continue
            date = meas.get("calendarDate")
            vo2 = meas.get("vo2MaxValue")
            if date and vo2 and vo2 > 10:  # Sanity — VO2 under 10 is impossible
                # If we already have a value for this date, keep the higher one
                if date not in out or out[date]["vo2Max"] < vo2:
                    out[date] = {"vo2Max": round(vo2, 1)}

    return out


# ─────────────────────────────────────────────────────────────────────
# MERGE & EXPORT
# ─────────────────────────────────────────────────────────────────────
def merge_by_date(*sources: dict[str, dict]) -> dict[str, dict]:
    """Combine per-date dicts from multiple sources into one."""
    combined: dict[str, dict] = defaultdict(dict)
    for src in sources:
        for date, fields in src.items():
            combined[date].update(fields)
    return dict(combined)


def build_export(
    sleep_data: dict, agg_data: dict, vo2_data: dict, source_path: Path
) -> dict:
    """Assemble the QuantHealth-compatible export JSON."""
    combined = merge_by_date(sleep_data, agg_data, vo2_data)

    # Sort by date ascending — v2.8's import prefers chronological
    entries = []
    for date in sorted(combined.keys()):
        entry = {"date": date}
        entry.update(combined[date])
        entries.append(entry)

    return {
        "source": "garmin",
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "schemaVersion": 1,
        "sourcePath": str(source_path),
        "stats": {
            "daysWithSleep": len(sleep_data),
            "daysWithWellness": len(agg_data),
            "vo2Measurements": len(vo2_data),
            "totalDays": len(combined),
        },
        "entries": entries,
    }


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Parse Garmin Connect data export into QuantHealth import JSON"
    )
    parser.add_argument(
        "folder", type=str,
        help="Path to the unzipped Garmin export folder (contains DI_CONNECT/)"
    )
    parser.add_argument(
        "--output", type=str,
        default=str(Path.home() / "Documents" / "QuantHealth"),
        help="Folder for the output JSON (default: ~/Documents/QuantHealth)"
    )
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.exists():
        print(f"ERROR: Folder not found: {root}")
        sys.exit(1)

    di_connect = root / "DI_CONNECT"
    if not di_connect.exists():
        print(f"ERROR: No DI_CONNECT folder inside {root}")
        print("       This doesn't look like a Garmin data export.")
        sys.exit(1)

    wellness_dir = di_connect / "DI-Connect-Wellness"
    agg_dir = di_connect / "DI-Connect-Aggregator"
    metrics_dir = di_connect / "DI-Connect-Metrics"

    print(f"\n┌{'─' * 58}┐")
    print(f"│ QuantHealth — Garmin Export Import                       │")
    print(f"└{'─' * 58}┘\n")
    print(f"  Source: {root}\n")

    # Parse each data source
    print("  Parsing sleep data...")
    sleep_data = parse_sleep_files(wellness_dir)
    print(f"    ✓ {len(sleep_data)} nights with sleep data")

    print("  Parsing wellness/stress aggregator...")
    agg_data = parse_aggregator_files(agg_dir)
    print(f"    ✓ {len(agg_data)} days with wellness data")

    print("  Parsing VO2 Max history...")
    vo2_data = parse_vo2_file(metrics_dir)
    print(f"    ✓ {len(vo2_data)} VO2 Max measurements")

    # Build and write export
    export = build_export(sleep_data, agg_data, vo2_data, root)

    # Summary of sample values from a recent entry
    if export["entries"]:
        recent = export["entries"][-1]
        print(f"\n  Sample (most recent day, {recent['date']}):")
        for k, v in recent.items():
            if k == "date":
                continue
            print(f"    {k}: {v}")

    # Write output
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_file = out_dir / f"garmin-export-{today}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2)

    size_kb = out_file.stat().st_size / 1024
    print(f"\n  ✓ Wrote {out_file}")
    print(f"    {size_kb:.1f} KB, {len(export['entries'])} day-entries")
    print(f"\n  Stats:")
    for k, v in export["stats"].items():
        print(f"    {k}: {v}")

    print(f"\n  Next: transfer this file to your phone and")
    print(f"        open QuantHealth → Sync → Restore/Import → select it.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(130)
    except Exception as e:
        print(f"\n  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
