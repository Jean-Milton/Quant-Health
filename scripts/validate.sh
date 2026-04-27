#!/usr/bin/env bash
# QuantHealth — validation harness
# Runs static checks on index.html before commit/deploy.
# Exit code: 0 = pass, non-zero = fail.

set -u  # error on unset vars (but not on command failures, we handle those manually)

# ─────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HTML_FILE="${HTML_FILE:-$REPO_ROOT/index.html}"

# Colors (only if stdout is a terminal)
if [ -t 1 ]; then
  GREEN='\033[0;32m'
  RED='\033[0;31m'
  YELLOW='\033[0;33m'
  CYAN='\033[0;36m'
  RESET='\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; CYAN=''; RESET=''
fi

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() { printf "  ${GREEN}✓${RESET} %s\n" "$1"; PASS_COUNT=$((PASS_COUNT+1)); }
fail() { printf "  ${RED}✗${RESET} %s\n" "$1"; FAIL_COUNT=$((FAIL_COUNT+1)); }
warn() { printf "  ${YELLOW}⚠${RESET} %s\n" "$1"; WARN_COUNT=$((WARN_COUNT+1)); }
section() { printf "\n${CYAN}── %s ──${RESET}\n" "$1"; }

# ─────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────
echo
echo "QuantHealth — Validation Harness"
echo "================================="
echo "Target: $HTML_FILE"

if [ ! -f "$HTML_FILE" ]; then
  printf "${RED}ERROR: index.html not found at $HTML_FILE${RESET}\n"
  exit 1
fi

LINES=$(wc -l < "$HTML_FILE")
SIZE_KB=$(($(wc -c < "$HTML_FILE") / 1024))
echo "Size: ${LINES} lines, ${SIZE_KB} KB"

# ─────────────────────────────────────────────────────────────
# Check 1: JavaScript syntax parses cleanly
# ─────────────────────────────────────────────────────────────
section "JavaScript syntax"

if ! command -v node >/dev/null 2>&1; then
  warn "Node.js not installed — skipping JS parse check"
else
  PARSE_RESULT=$(node -e "
    const fs = require('fs');
    const html = fs.readFileSync('$HTML_FILE', 'utf8');
    const matches = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)];
    if (matches.length === 0) {
      console.error('NO_SCRIPT');
      process.exit(2);
    }
    let combined = matches.map(m => m[1]).join('\n;\n');
    try {
      new Function(combined);
      console.log('OK');
    } catch (e) {
      console.error('PARSE_ERROR:' + e.message);
      process.exit(3);
    }
  " 2>&1)

  if echo "$PARSE_RESULT" | grep -q "^OK"; then
    pass "JS parses cleanly"
  elif echo "$PARSE_RESULT" | grep -q "NO_SCRIPT"; then
    fail "No <script> block found in HTML"
  else
    fail "JS parse error: $(echo "$PARSE_RESULT" | head -1)"
  fi
fi

# ─────────────────────────────────────────────────────────────
# Check 2: Handler coverage (every onclick/onchange/oninput → defined function)
# ─────────────────────────────────────────────────────────────
section "Handler coverage"

if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not installed — skipping handler coverage"
else
  HANDLER_RESULT=$(python3 -c "
import re, sys
with open('$HTML_FILE', 'r') as f:
    html = f.read()
handlers = set()
for attr in ('onclick', 'onchange', 'oninput'):
    handlers.update(re.findall(attr + r'=\"(\w+)\(', html))
funcs = set(re.findall(r'function\s+(\w+)\s*\(', html))
missing = handlers - funcs
if missing:
    print('MISSING:' + ','.join(sorted(missing)))
    sys.exit(1)
else:
    print('OK:%d handlers,%d functions' % (len(handlers), len(funcs)))
")

  if echo "$HANDLER_RESULT" | grep -q "^OK"; then
    COUNTS=$(echo "$HANDLER_RESULT" | sed 's/OK://')
    pass "Handler coverage clean ($COUNTS)"
  else
    MISSING=$(echo "$HANDLER_RESULT" | sed 's/MISSING://')
    fail "Orphan handlers (no matching function): $MISSING"
  fi
fi

# ─────────────────────────────────────────────────────────────
# Check 3: Stable Core — fetch() count
# Only the service worker registration may use fetch.
# Any extra fetch is a violation of "no external API calls."
# ─────────────────────────────────────────────────────────────
section "Stable Core (no external API calls)"

FETCH_COUNT=$(grep -c "fetch(" "$HTML_FILE" || true)

if [ "$FETCH_COUNT" -eq 0 ]; then
  pass "No fetch() calls (Stable Core preserved)"
elif [ "$FETCH_COUNT" -eq 1 ]; then
  pass "Exactly 1 fetch() (service worker only — Stable Core preserved)"
else
  fail "Found $FETCH_COUNT fetch() calls — only 1 allowed (service worker)"
  echo "    Locations:"
  grep -n "fetch(" "$HTML_FILE" | head -5 | sed 's/^/      /'
fi

# ─────────────────────────────────────────────────────────────
# Check 4: Required IDs / structural integrity
# ─────────────────────────────────────────────────────────────
section "Structural integrity"

REQUIRED_IDS=(
  "page-today" "page-checkin" "page-program" "page-trends" "page-history" "page-sync"
  "complianceStripOutput" "dayWorkoutOutput" "dayNav"
)

MISSING_IDS=()
for id in "${REQUIRED_IDS[@]}"; do
  if ! grep -q "id=\"$id\"" "$HTML_FILE"; then
    MISSING_IDS+=("$id")
  fi
done

if [ ${#MISSING_IDS[@]} -eq 0 ]; then
  pass "All required element IDs present (${#REQUIRED_IDS[@]} checked)"
else
  fail "Missing required IDs: ${MISSING_IDS[*]}"
fi

# ─────────────────────────────────────────────────────────────
# Check 5: Migration test (load fixture v2.4 data, run migrations)
# ─────────────────────────────────────────────────────────────
section "Migration test (v2.4 → current)"

FIXTURE="$REPO_ROOT/test-fixtures/v24-sample.json"

if [ ! -f "$FIXTURE" ]; then
  warn "No fixture at $FIXTURE — skipping migration test"
elif ! command -v node >/dev/null 2>&1; then
  warn "Node.js not installed — skipping migration test"
else
  MIGRATION_RESULT=$(node -e "
    const fs = require('fs');
    const html = fs.readFileSync('$HTML_FILE', 'utf8');
    const fixture = JSON.parse(fs.readFileSync('$FIXTURE', 'utf8'));

    // Pull migrate() body. The function is defined inside an IIFE.
    // We extract the migrate function and execute it on the fixture.
    const migrateMatch = html.match(/const migrate = \(data\) => \{([\s\S]+?)\n  \};/);
    if (!migrateMatch) {
      console.error('CANNOT_FIND_MIGRATE');
      process.exit(2);
    }

    let migrate;
    try {
      migrate = new Function('data', migrateMatch[1] + '\nreturn data;');
    } catch (e) {
      console.error('BUILD_ERROR:' + e.message);
      process.exit(3);
    }

    let result;
    try {
      result = migrate(JSON.parse(JSON.stringify(fixture)));
    } catch (e) {
      console.error('RUN_ERROR:' + e.message);
      process.exit(4);
    }

    // Schema assertions on the migrated object
    const checks = [];
    if (!Array.isArray(result.entries)) checks.push('entries not array');
    if (!result.completedWorkouts) checks.push('completedWorkouts missing');
    if (!result.userFoods) checks.push('userFoods missing');
    if (!result.settings) checks.push('settings missing');
    if (!Array.isArray(result.importHistory)) checks.push('importHistory missing');
    if (!result.workoutLogs) checks.push('workoutLogs missing');

    // Per-entry assertions
    for (const e of result.entries) {
      if (e.activities === undefined) { checks.push('entry missing activities[]'); break; }
      if (e.foodEntries === undefined) { checks.push('entry missing foodEntries[]'); break; }
      if (e.water === undefined) { checks.push('entry missing water field'); break; }
      // v2.8b cleanup
      if (e.sleepScore !== undefined) { checks.push('legacy sleepScore not removed'); break; }
      if (e.bodyBatteryLow !== undefined) { checks.push('legacy bodyBatteryLow not removed'); break; }
      // v2.8b additions
      if (e.sleepHours === undefined) { checks.push('entry missing sleepHours'); break; }
      if (e.steps === undefined) { checks.push('entry missing steps'); break; }
    }

    if (checks.length === 0) {
      console.log('OK:' + result.entries.length + ' entries migrated');
    } else {
      console.error('FAILED:' + checks.join('; '));
      process.exit(5);
    }
  " 2>&1)

  if echo "$MIGRATION_RESULT" | grep -q "^OK:"; then
    DETAILS=$(echo "$MIGRATION_RESULT" | sed 's/OK://')
    pass "Migration v2.4 → current ($DETAILS)"
  else
    fail "Migration test failed: $MIGRATION_RESULT"
  fi
fi

# ─────────────────────────────────────────────────────────────
# Check 6: Forbidden patterns
# ─────────────────────────────────────────────────────────────
section "Forbidden patterns"

# console.log in production HTML is acceptable, but TODO/FIXME left in shipped code is a code smell
TODO_COUNT=$(grep -c -E "TODO|FIXME|XXX" "$HTML_FILE" || true)
if [ "$TODO_COUNT" -eq 0 ]; then
  pass "No TODO/FIXME/XXX markers"
else
  warn "$TODO_COUNT TODO/FIXME/XXX markers found"
fi

# Localhost URLs that escape into production
if grep -q "localhost" "$HTML_FILE"; then
  fail "localhost URL found — would not work in production"
else
  pass "No localhost references"
fi

# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────
echo
echo "================================="
printf "Result: ${GREEN}%d passed${RESET}, ${RED}%d failed${RESET}, ${YELLOW}%d warnings${RESET}\n" \
  "$PASS_COUNT" "$FAIL_COUNT" "$WARN_COUNT"
echo

if [ "$FAIL_COUNT" -gt 0 ]; then
  printf "${RED}✗ Validation failed${RESET}\n"
  exit 1
else
  printf "${GREEN}✓ All checks passed${RESET}\n"
  exit 0
fi
