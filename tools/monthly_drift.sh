#!/usr/bin/env bash
# Monthly early-warning drift check (READ-ONLY). Compares each card's QBO register
# balance to its real statement balance and logs any drift over the threshold, so a
# small divergence is caught before it compounds into an annual five-figure phantom.
#
# It runs against whatever exports are sitting in the PERIOD folders below, so keep a
# folder of fresh QBO register exports per entity (see FOLDER_STANDARD.md). The check
# never edits QuickBooks; it only reads and reports.
#
# Edit the two PERIOD_* paths and the YEAR, then install the schedule (see tools/README.md).
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
PY="$REPO/venv/bin/python"
YEAR="$(date +%Y)"

# --- EDIT THESE to point at the folder holding each entity's latest QBO register exports ---
PERIOD_ST_GERTRUDE="$HOME/Desktop/Sunday 2:28 folder"
PERIOD_PALEO=""   # set once Paleo exports have a home

LOG="$REPO/tools/drift_logs/drift_$(date +%Y-%m-%d).log"
{
  echo "===== Monthly drift check — $(date) ====="
  if [ -n "$PERIOD_ST_GERTRUDE" ] && [ -d "$PERIOD_ST_GERTRUDE" ]; then
    echo "--- st_gertrude ---"
    "$PY" "$REPO/scripts/drift_check.py" --entity st_gertrude --year "$YEAR" --period "$PERIOD_ST_GERTRUDE" || echo "(drift over threshold — investigate)"
  fi
  if [ -n "$PERIOD_PALEO" ] && [ -d "$PERIOD_PALEO" ]; then
    echo "--- certified_paleo ---"
    "$PY" "$REPO/scripts/drift_check.py" --entity certified_paleo --year "$YEAR" --period "$PERIOD_PALEO" || echo "(drift over threshold — investigate)"
  fi
  echo "===== done ====="
} 2>&1 | tee -a "$LOG"
