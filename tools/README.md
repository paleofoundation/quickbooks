# Monthly drift check (early-warning automation)

The duplicated-account phantom grows silently. This catches it early: once a month it compares each
card's QuickBooks register balance to its real statement balance and logs any drift over the threshold
(default $500, set in `config/entities.yaml`). It is **READ-ONLY** — it never touches QuickBooks.

## What you maintain
Keep a folder of fresh **QBO register exports** for each entity (see `../FOLDER_STANDARD.md`). The check
runs against whatever is in that folder, so it is only as current as your last export.

## Set it up
1. Edit `monthly_drift.sh` — set `PERIOD_ST_GERTRUDE` (and `PERIOD_PALEO` once Paleo has an export folder).
2. Try it by hand first:
   ```
   bash tools/monthly_drift.sh
   ```
   You should see a per-card drift table; output is also appended to `tools/drift_logs/`.
3. Install the monthly schedule (runs the 1st of each month, 9am):
   ```
   cp tools/com.nonprofitclose.driftcheck.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.nonprofitclose.driftcheck.plist
   ```
   Remove it later with:
   ```
   launchctl unload ~/Library/LaunchAgents/com.nonprofitclose.driftcheck.plist
   ```

## Reading the result
A `FAIL` line means a card's book balance and statement balance diverge by more than the threshold —
investigate that month before it compounds (it is almost always a new duplicate-account / re-keyed entry).
The fix is always executed by a human in QuickBooks (Leigh Ann), never by this tool.
