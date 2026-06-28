# nonprofit-close-reconciliation

Repeatable close for a small entity: reconcile QuickBooks to the real bank/card statements, find the
recurring duplicated-account phantom, bridge the P&L to its expected net (entity-type aware), run the
forensic-controls layer, and produce a close package (workbook + memo). Built from the Gardens of
St. Gertrude 2025 forensic close; the same engine drives the Paleo Foundation (S-corp) redo.

**READ-ONLY tool.** It reads QBO exports and produces worksheets/memos. It never edits QuickBooks — a
human (the bookkeeper) executes every deletion/consolidation.

- `SKILL.md` — the playbook (also the Cowork skill entry point); start here.
- `config/entities.yaml` — entity facts; edit once per year / when accounts change.
- `FOLDER_STANDARD.md` — exactly which files to drop each period.
- `PREVENTION_SOP.md` — how to stop the problem at the source (process + access controls).
- `CONTROLS_SOP.md` — the forensic-controls layer and the segregation-of-duties policy.
- `CHANGELOG.md` — what changed from v1 to v2.
- `scripts/` — `close.py` (driver) + `_common.py`, `reconcile_cards.py`, `bridge_to_zero.py`,
  `verify.py`, and the installed controls modules.

---

## Quick start (no developer experience needed)

You run two shell commands. The first builds the environment once; the second runs a close.

### 1. One-time setup
```bash
./setup.sh
```
Creates a self-contained `./venv` and installs everything (pandas, openpyxl, xlrd, numbers-parser,
pyyaml, pdfplumber, python-docx). Safe to re-run. Do **not** `pip install` by hand.

### 2. Run a close
```bash
./close.sh --entity st_gertrude --year 2025 --period "<period folder>"
```
`--entity` matches a key in `config/entities.yaml` (`st_gertrude`, `certified_paleo`). `--period` is
the folder holding that period's exports (see `FOLDER_STANDARD.md`). `close.sh` auto-runs `setup.sh`
on first use.

Try it now on the checked-in real data:
```bash
./close.sh --entity st_gertrude --year 2025 --period "tests/fixtures/st_gertrude_2025"
```

---

## What PASS / FAIL means

At the end of every run `close.py` prints `VERIFY: PASS` or `VERIFY: FAIL` and exits 0 / nonzero.

- **PASS** — the close is internally consistent for this entity type. For `break_even`: corrected net
  ties to the target (0) within tolerance, the implied owner contribution is not negative, and the P&L
  cross-foots. For `for_profit`: the P&L cross-foots, the bridge did not invent a contribution plug,
  and per-account phantoms are reported (to be cleared to 0 during cleanup).
- **FAIL** — something doesn't reconcile. Common causes: the break-even assumption is wrong (negative
  implied contribution → switch `funding_model` to `for_profit`), the P&L doesn't cross-foot, or the
  bridge isn't reproducible. The failure lines tell you which. **Never ship a close on a FAIL.**

A `NOTE:` about back-dated register rows is informational (a tampering tell to review in the controls
tab), not a failure.

---

## Where outputs go

Into `<period folder>/close_<entity>_<year>/`:
- `close_summary.json` — machine-readable result (per-account reconciliation, bridge, controls, and a
  SHA-256 input manifest pinning every file read).
- the close workbook with tabs **Controls Summary, Bridge, Per-Account Reconciliation, Duplicate
  Delete-Set, Evidence Index**, and the findings memo (Section A bookkeeper fixes / Section B tax items).

These outputs are gitignored (`close_*/`, `close_summary.json`).

---

## The golden-master test (the accuracy guarantee)

A regression test runs the real engine on the checked-in 2025 St. Gertrude data and asserts it
reproduces the hand-verified forensic close to the cent (phantom $35,617.29, balance phantom
$47,213.18, owner contribution $6,709.35, corrected net $0, and the for-profit no-plug safety case).
Run it after any change:
```bash
./venv/bin/python tests/test_golden_master.py
```
Expected tail: `GOLDEN MASTER: ALL PASS`.

---

## Register the skill

```bash
./install.sh
```
Symlinks the repo into `~/.claude/skills/` so Claude Code discovers the
`nonprofit-close-reconciliation` skill. The repo stays the source of truth — edits here take effect
immediately.

---

## ⚠️ Privacy — do not push unscrubbed

`tests/fixtures/` contains **REAL financial data** (St. Gertrude 2025 registers, statements, P&L,
balance sheet). This repo is a private local tool. **Do not push it to a public remote without
scrubbing `tests/fixtures/`.** There is no configured remote by default; keep it that way unless the
fixtures are removed or sanitized first.
