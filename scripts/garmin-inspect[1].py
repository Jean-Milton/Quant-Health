"""
QuantHealth — Garmin Export Inspector
=====================================
Walks every folder in the unzipped Garmin export, reads every JSON file,
and reports:
  - All unique field names found
  - Which files contain fields matching keywords of interest
    (REM, stress, battery, HRV, sleep score, etc.)
  - Sample values for flagged fields

Run once against your unzipped Garmin export folder to confirm what data
is actually present before committing to the v2.10 import script.

Usage:
    python garmin-inspect.py "C:\\path\\to\\unzipped\\garmin\\export"

Output:
    - Summary printed to terminal
    - Full report saved to garmin-inspect-report.json in current directory
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path


# Keywords to flag (case-insensitive). Any field name containing these substrings
# is highlighted as potentially interesting.
KEYWORDS_OF_INTEREST = [
    "rem",           # REM sleep
    "stress",        # stress score
    "battery",       # body battery
    "hrv",           # heart rate variability
    "score",         # any *Score field (sleepScore, recoveryScore, etc.)
    "restingheart",  # resting HR
    "resting_heart",
    "vo2",           # VO2 max
    "steps",         # daily steps
    "spo2",          # blood oxygen
    "breath",        # breathing rate
    "respiration",
    "intensity",     # intensity minutes
    "recovery",      # recovery time/score
    "training",      # training status
]


def flatten_keys(obj, path="", keys=None):
    """Recursively collect all field paths in a nested JSON structure."""
    if keys is None:
        keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else k
            keys.add(new_path)
            flatten_keys(v, new_path, keys)
    elif isinstance(obj, list):
        # For lists, inspect the first element as a representative
        if obj and isinstance(obj[0], (dict, list)):
            flatten_keys(obj[0], path + "[]", keys)
    return keys


def sample_value(obj, target_key, max_samples=3):
    """Find up to N sample values for a given key (anywhere in the structure)."""
    samples = []

    def walk(o):
        if len(samples) >= max_samples:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if len(samples) >= max_samples:
                    return
                # Match by last segment of the key path
                if k == target_key.split(".")[-1].rstrip("[]"):
                    if v is not None and not isinstance(v, (dict, list)):
                        samples.append(v)
                walk(v)
        elif isinstance(o, list):
            for item in o:
                if len(samples) >= max_samples:
                    return
                walk(item)

    walk(obj)
    return samples


def matches_keyword(field_name, keywords):
    """Check if a field name contains any of the keywords (case-insensitive)."""
    name_lower = field_name.lower()
    matched = []
    for kw in keywords:
        if kw in name_lower:
            matched.append(kw)
    return matched


def main():
    parser = argparse.ArgumentParser(description="Inspect Garmin export folder for field structure")
    parser.add_argument("folder", type=str, help="Path to the unzipped Garmin export folder")
    parser.add_argument("--output", type=str, default="garmin-inspect-report.json",
                        help="Where to write the full report (default: ./garmin-inspect-report.json)")
    parser.add_argument("--max-samples", type=int, default=3,
                        help="Max sample values to capture per flagged field (default: 3)")
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.exists():
        print(f"ERROR: Folder not found: {root}")
        sys.exit(1)

    print(f"\n┌{'─' * 58}┐")
    print(f"│ QuantHealth — Garmin Export Inspector                    │")
    print(f"└{'─' * 58}┘\n")
    print(f"  Scanning: {root}")

    # Collect data
    all_fields = defaultdict(set)  # field_path → set of file paths containing it
    flagged_samples = defaultdict(list)  # field_path → list of (file, sample_values)
    file_count = 0
    error_count = 0
    total_size = 0
    extensions_seen = defaultdict(int)

    for dirpath, dirnames, filenames in os.walk(root):
        for fname in filenames:
            fpath = Path(dirpath) / fname
            ext = fpath.suffix.lower()
            extensions_seen[ext] += 1
            if ext != ".json":
                continue

            rel_path = fpath.relative_to(root)
            total_size += fpath.stat().st_size

            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                error_count += 1
                continue
            except Exception as e:
                error_count += 1
                continue

            file_count += 1

            # Collect all field paths in this file
            file_fields = flatten_keys(data)

            for field in file_fields:
                all_fields[field].add(str(rel_path))

                # Check if field name matches any keyword
                field_leaf = field.split(".")[-1].rstrip("[]")
                matched = matches_keyword(field_leaf, KEYWORDS_OF_INTEREST)
                if matched:
                    samples = sample_value(data, field, args.max_samples)
                    if samples:
                        flagged_samples[field].append({
                            "file": str(rel_path),
                            "matched_keywords": matched,
                            "samples": [str(s)[:80] for s in samples],
                        })

    # ─────────────────────────────────────────────────────────
    # Report
    # ─────────────────────────────────────────────────────────
    print(f"\n  Scanned {file_count} JSON files ({total_size / 1024 / 1024:.1f} MB)")
    if error_count:
        print(f"  ⚠ Skipped {error_count} files that failed to parse")
    print(f"  Total unique field paths: {len(all_fields)}")

    # File extension breakdown
    print(f"\n  File types in export:")
    for ext, count in sorted(extensions_seen.items(), key=lambda x: -x[1]):
        ext_display = ext if ext else "(no extension)"
        print(f"    {ext_display:20s} {count:6d} files")

    # Flagged field report
    print(f"\n  ═══ FIELDS OF INTEREST ═══")
    if not flagged_samples:
        print(f"  No fields matched any keyword. REM / stress / body battery likely not in export.")
    else:
        # Group by the keyword that matched, for nicer output
        by_keyword = defaultdict(dict)
        for field, occurrences in flagged_samples.items():
            for occ in occurrences:
                for kw in occ["matched_keywords"]:
                    if field not in by_keyword[kw]:
                        by_keyword[kw][field] = []
                    by_keyword[kw][field].append(occ)

        for kw in sorted(by_keyword.keys()):
            print(f"\n  ▸ Keyword: {kw.upper()}")
            for field, occs in sorted(by_keyword[kw].items()):
                first_samples = occs[0]["samples"][:3] if occs else []
                file_count_for_field = len(set(o["file"] for o in occs))
                print(f"      {field}")
                print(f"        in {file_count_for_field} file(s), sample values: {first_samples}")
                # Show up to 2 example files
                example_files = list(set(o["file"] for o in occs))[:2]
                for ef in example_files:
                    print(f"          └ {ef}")

    # Specifically flag if REM was not found
    print(f"\n  ═══ REM CHECK ═══")
    rem_fields = [f for f in all_fields if "rem" in f.lower() and "rem" in f.lower().split(".")[-1]]
    if rem_fields:
        print(f"  ✓ REM field(s) found:")
        for f in rem_fields:
            print(f"      {f}")
    else:
        print(f"  ✗ No REM field found in any JSON file.")
        print(f"    Garmin's Connect app must compute REM client-side or fetch it")
        print(f"    from a server endpoint not included in the data export.")

    # Write full report
    report = {
        "folder_scanned": str(root),
        "file_count": file_count,
        "error_count": error_count,
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "extensions": dict(extensions_seen),
        "rem_fields_found": rem_fields,
        "all_field_paths_count": len(all_fields),
        "all_field_paths": sorted(all_fields.keys()),
        "flagged_fields": {
            field: occurrences
            for field, occurrences in flagged_samples.items()
        },
    }

    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  ✓ Full report written to {out_path.absolute()}")
    print(f"    (Send this file back if you want me to review the complete picture.)\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
