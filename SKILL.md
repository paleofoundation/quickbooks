---
name: nonprofit-close-reconciliation
description: Reconcile a small nonprofit's QuickBooks Online books to the real bank/card statements and bridge the P&L to its expected net (typically $0 for break-even-funded orgs). Use for year-end or monthly close when you need to detect duplicated credit-card accounts / re-keyed transactions (phantom balances), quantify the duplicate set for the bookkeeper, reconcile owner/related-party in-kind contributions, match receipts to bookings, and produce a close package (workbook + memo). Triggers: "close the books", "reconcile St. Gertrude / the rescue", "why is there a loss", "QBO doesn't match the statement", "phantom balance", "duplicate charges", "book vs real".
---

# Nonprofit / Business Close Reconciliation (v2)

A repeatable, **config-driven** procedure for closing a small entity's books by reconciling
QuickBooks (the "book") against the real bank/card statements (the "truth"), finding the recurring
duplicated-account problem, proving the P&L nets to its expected result, and surfacing the forensic
red flags a tampered file leaves behind.

It was built from the Gardens of St. Gertrude 2025 close, where a single Capital One account recorded
twice in QBO (with manually re-keyed bank-feed charges) created a **$47,213.18** year-end balance
phantom and a fake **$42,326.64** loss. The real result was **$0** once (a) the **$35,617.29**
duplicate expense was removed and (b) the **$6,709.35** owner-paid cost was booked as a contribution.
The same engine drives the Paleo Foundation (S-corp) redo via a `for_profit` profile.

---

## INVARIANTS (never violate these)

1. **READ-ONLY.** Nothing here edits QuickBooks or calls any QBO write/import/update tool. The skill
   READS exports and PRODUCES worksheets/memos/findings only. A human (Leigh Ann) executes every
   deletion/consolidation. No automation touches the live file.
2. **Flag-but-never-decide tax positions.** Any tax item is surfaced "for Anasheh sign-off — not tax
   advice", status **OPEN**. The skill computes the mechanical inputs; the attorney chooses the
   treatment.
3. **FX-tolerant matching.** Re-keyed duplicates are NOT penny-identical (they were re-entered at EUR
   × FX). Match by date + payee + near-amount, never on exact amount. An exact-amount test found only
   3 of ~195 duplicates.
4. **Never bulk-delete the inflated account.** Part of its activity is the SOLE legitimate booking of
   real charges. Only firm-matched duplicate entries are deleted, reviewed line by line.
5. **Duplicate TOTAL is exact; per-line split is an ESTIMATE.** The control total (book P&L charges −
   real charges) is exact. The matched-pair list is a tiered estimate for human review. Present it
   that way; the residual is "investigate", not "delete".
6. **Owner contribution is flagged, not classified.** The under-recorded owner in-kind amount is
   quantified; whether it is a **contribution vs a loan** is a tax decision deferred to the attorney.

---

## The close test (the goal function) — ENTITY-TYPE-AWARE

The close "passes" on internal consistency, and the test depends on `funding_model` in
`config/entities.yaml`:

### `break_even` (e.g. Gardens of St. Gertrude — a contribution-funded 501(c)(3) sized to net $0)
- **Net ties to target.** `corrected_net == net_income_target` (0) within `close_tolerance`.
- **No negative plug.** The implied owner contribution must NOT be negative. A negative plug means the
  break-even assumption is wrong for this entity — `verify.py` FAILS and tells you to switch to
  `for_profit`. The loss decomposes into exactly two fixable parts:
  `loss = duplicate expense (remove) + under-recorded contribution income (add)`.
- **P&L cross-foots.**

### `for_profit` (e.g. Certified Paleo / Paleo Foundation — an S-corp with a real nonzero net)
- **Per-account phantom clears to 0.** Each card's balance phantom is reconciled to the real
  statement; the close is verified on per-account reconciliation + controls, NOT on net == a target.
- **Never plug.** Only PROVEN duplicate expense is removed. The remaining result is the REAL net —
  the bridge does NOT invent a contribution. `verify.py` FAILS if a for-profit bridge ever produced an
  `owner_contrib_to_record`. Unexplained variance is surfaced for human classification.
- **P&L cross-foots.**

---

## The ONE command

```bash
./close.sh --entity <entity> --year <year> --period "<period folder>"
# e.g.
./close.sh --entity st_gertrude --year 2025 --period "tests/fixtures/st_gertrude_2025"
```

`close.sh` builds the venv on first run (calls `setup.sh`), then runs `scripts/close.py`, which:
reads `config/entities.yaml`, auto-discovers the period's exports, and runs the whole pipeline. It
writes `close_<entity>_<year>/close_summary.json` plus the package into the period folder, and exits
nonzero if `verify` fails.

### The pipeline (what `close.py` does, in order)
1. **Reconcile** every configured account (book vs real): per-card P&L phantom, balance phantom
   (carry-in + added-in-year), and off-period (back-/future-dated) row counts.
2. **Pair duplicates** for any two-accounts-one-card account: fuzzy date+payee+near-amount matching
   into a tiered, control-total-checked delete-set.
3. **Bridge** the P&L (entity-type aware) from booked net to corrected net.
4. **Controls** (each runs only if its module is installed; a controls check NEVER crashes the close):
   - `audit_log` — who-did-what / shared-login and off-hours analysis from the QBO Audit Log export.
   - `forced_reconciliation_check` — Opening Balance Equity / Reconciliation Discrepancy / prior-period
     plug footprints (Bobbie's forced reconciliations).
   - `duplicate_scan` — independent re-keyed-duplicate sweep.
   - `compliance_checks` — gross-receipts / total-assets / FBAR / Schedule L mechanical flags.
   - `form990_selector` — computes the inputs that point at a 990 form (final choice deferred).
5. **Verify** — `verify.py` independently re-derives the headline numbers and gates the close.
6. **build_package + genmemo** — the close workbook (Controls Summary, Bridge, Per-Account Reconciliation,
   Duplicate Delete-Set, Evidence Index) and the findings memo split into Section A bookkeeper fixes vs
   Section B tax items (each tax item flagged OPEN for Anasheh).

The granular tools (`reconcile_cards.py`, `bridge_to_zero.py`, `verify.py`) can also be run standalone
for one account — see their `--help`.

---

## DEFERRAL MATRIX — what the skill ENCODES vs what it DEFERS

The skill does the **mechanical** computation and stops at the **judgment** line. Everything in the
DEFER column is flagged for **Anasheh (tax attorney)** with status OPEN — never decided here.

| ENCODE (mechanical — the skill computes it)                                  | DEFER (judgment — flag for Anasheh, status OPEN)                         |
|------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| Per-card book-vs-real reconciliation (charge flow + ending balance)          | The final 990 form (990 / 990-EZ / 990-N) — skill computes inputs only   |
| Balance phantom & P&L phantom (carry-in + added-in-year)                      | Owner in-kind: **contribution vs loan** treatment                       |
| Duplicate tiering (Tier 1–3) and the exact control total                     | FEIE applied to S-corp **K-1 pass-through** (aggressive — confirm)       |
| Gross-receipts & total-assets computation (on **corrected** figures)         | Reasonable-officer-compensation **dollar amount** (IRC 162 reclass)     |
| FBAR / FinCEN-114 **balance trigger** (foreign accounts present)             |                                                                         |
| Schedule L **drift** (QBO vs return) magnitude                               |                                                                         |
| Reasonable-comp **red flag** (distributions + $0 wages pattern)              |                                                                         |

The skill quantifies the reasonable-comp red flag and the FEIE exposure; it never states the right
dollar figure or the right position. That is the attorney's call.

---

## Inputs — the folder standard

Everything is driven off one folder per period. See `FOLDER_STANDARD.md`. The scripts auto-discover
files by convention (tolerant of space/underscore differences). In short, per period you need: the QBO
register `.xls` for each credit-card account, the QBO P&L and Balance Sheet CSVs, the real
transaction CSV per bank/card, a `statement_balances.csv` of real month-end balances (PDFs don't parse
reliably), owner (Bank of Cyprus, personal-card) statements, and the QBO **Audit Log** export.
Standardizing this is the #1 speed win.

The QBO connector and report widgets do **not** return numbers to the model — CSV/register exports are
mandatory, and the connector works one company at a time.

---

## Configure once: `config/entities.yaml`

The single source of truth the scripts READ. Per entity it captures: `funding_model`
(`break_even` | `for_profit`), `net_income_target`, the account list (real-vs-QBO card mapping,
`qbo_recorded_as_two`, `feed_card`/`manual_dupe_card`, `statement_balance_eoy`, `carry_in_prior_year`),
funders, the compliance profile, and the controls/expected-users block. Add an account here — no code
change needed; the engine loops the list.

---

## Guardrails (restate the invariants at the worksheet level)

- **Never bulk-delete the inflated account.** Delete only firm-matched duplicate entries (Tiers 1–3),
  reviewed line by line, then reconcile the single consolidated account to the statements.
- **Present the duplicate total as exact and the per-line split as an estimate.** Residual =
  investigate; do not delete the residual.
- **Owner / related-party contributions are a tax decision (contribution vs loan).** As a contribution
  it boosts the public-support test and gives the owner a deduction if they itemize; as a loan it is a
  Schedule L related-party payable. Flag the amount; defer the treatment.
- **Personal cards are not the org's.** Owner-paid program costs enter the books only as
  reimbursements or in-kind contributions; check for leakage and for the matching contribution income.
- **AutoPay payoffs are already income.** For St. Gertrude, AutoPay payoffs from Chase Cat Checking are
  already captured as "Paleo's Cont." — do NOT re-book them as a separate contribution.

---

## Root cause & prevention

This problem recurs monthly until the bookkeeping process and access controls change. See
`PREVENTION_SOP.md` (one QBO account per real card, bank-feed-only, monthly reconcile-to-statement,
the credential/access-controls section that names the Alison/Bobbie failure modes, and the automated
monthly drift check) and `CONTROLS_SOP.md` (what the forensic-controls layer detects and the
remediation each exception implies).
