# Controls SOP — the forensic-controls layer

The reconciliation proves the numbers; the controls layer proves the **process** that produced them
can be trusted. These checks run inside `close.py` (each only if its module is installed) and feed the
controls tab of the close package. Every check is **READ-ONLY** and **never crashes the close** — if a
controls module raises, `close.py` records the error and continues. Findings are surfaced for a human;
exceptions are flagged, never auto-resolved. Tax-flavored exceptions are marked "for Anasheh — status
OPEN".

The two motivating failure modes (see `PREVENTION_SOP.md`): **"Alison"** logging in AS Kimberly
(shared credentials, no audit trail) and **"Bobbie"** forcing reconciliations (Opening Balance Equity
plugs, prior-period adjustments, deleted accounts).

---

## `audit_log.py` — who-did-what

**Reads:** the QBO **Audit Log** export (see `FOLDER_STANDARD.md`) plus the `controls` block in
`config/entities.yaml` (`expected_users`, `known_unauthorized`, `mfa_required`, `closed_periods`).

**Detects:**
- Activity by users not in `expected_users`, or under a login flagged in `known_unauthorized` (the
  Alison-as-Kimberly window).
- Entries created/edited under a single login that the shared-credential history makes untrustworthy —
  i.e. "who entered this" cannot be taken at face value from the user field.
- Back-dating: entries dated into a `closed_period` after that period was closed.
- Off-hours / bulk edit bursts consistent with a forced cleanup.

**What an exception means:** the attribution in QBO is unreliable for those rows; you cannot assume the
named user actually made the entry. **Remediation:** treat the flagged rows as unverified provenance —
corroborate each against statements/receipts before relying on it; rotate shared credentials and
re-issue named logins (Prevention SOP control 1); set/raise the closing-date password.

---

## `forced_reconciliation_check.py` — Bobbie's footprints

**Reads:** the QBO Balance Sheet CSV (via `load_balance_sheet`, which surfaces `_control_flags`) and
the registers' off-period rows from the reconciliation.

**Detects** the residue a forced/garbage reconciliation leaves behind — the
`CONTROL_FLAG_ACCOUNTS`: **Opening Balance Equity**, **Reconciliation Discrepancies**, **Ask My
Accountant**, **Uncategorized Asset** with a nonzero balance; plus **prior-period adjustments** and
**back-dated register rows** (counted as `off_period_backdated_rows`). At St. Gertrude the Balance
Sheet still shows Opening balance equity −854.20, Prior period adjustments −9,049.96, and a "Personal
expenses (deleted)" line.

**What an exception means:** a balance was plugged rather than reconciled — the books were forced to
balance by dumping a difference into an equity/suspense account or by back-dating. These are NOT real
transactions; they mask the true variance. **Remediation:** trace each plug to what it was hiding
(usually the duplicate phantom), reverse the plug as part of the cleanup, and re-reconcile to the
statement. Do this BEFORE trusting any equity or retained-earnings figure. Never carry a plug forward.

---

## `duplicate_scan.py` — independent re-keyed-duplicate sweep

**Reads:** the registers; runs the same FX-tolerant date+payee+near-amount pairing as the close, as an
independent cross-check on the `pair_duplicates` delete-set.

**What an exception means:** candidate duplicate entries exist. **Remediation:** review the tiered
list line by line (Tier 1 = strongest). The control total is exact; the per-line split is an estimate;
the residual is "investigate", not "delete". **Never bulk-delete the inflated account** — part of it is
the sole legitimate booking of real charges.

---

## `compliance_checks.py` — mechanical tax-input flags

**Reads:** the `compliance_profile` / `tax_context` in `config/entities.yaml` and the corrected
figures from the bridge. **Computes (mechanical only):** gross receipts and total assets on the
**corrected** basis, the **FBAR / FinCEN-114 balance trigger** (foreign accounts present — Cyprus),
**Schedule L drift** (QBO vs return), and the **reasonable-comp red flag** (distributions + $0 wages
pattern). Each is emitted "for Anasheh — status OPEN". The skill never states the right dollar amount
or the right position; see the DEFERRAL MATRIX in `SKILL.md`.

---

## `form990_selector.py` — points at a form, does not choose it

**Computes** the gross-receipts / total-assets inputs that determine which 990 series applies and
notes the tripwires (e.g. St. Gertrude is over the $50K ceiling, so 990-N is wrong; historically
filing 990-N risks revocation). **The final form choice is deferred to Anasheh.**

---

## Segregation of duties & close cadence (policy)

The forensic tools assume — and the SOP requires — that no one person both records transactions and
blesses the close.

- **Bookkeeper (Leigh Ann)** records and reconciles, and EXECUTES every deletion/consolidation this
  skill recommends. This skill and Claude never edit QuickBooks.
- **Owners (Kimberly, Karen)** own admin: only they add/delete accounts or change settings; they
  review and approve the close.
- **Tax advisor (Anasheh)** signs off every tax position. Mechanical inputs come from the skill;
  the judgment is the attorney's.
- **No shared logins, MFA everywhere, accountant role-limited** (Prevention SOP).

**Close cadence:**
- **Monthly:** run `drift_check.py`; reconcile every account to its statement; investigate any drift
  over threshold the same month.
- **At each close:** run `./close.sh`, clear `VERIFY: FAIL`, review the controls tab, then set the
  QBO closing-date password and add the period to `controls.closed_periods` in `entities.yaml`.
- **Annually:** the full forensic close + package + memo, with the tax items routed to Anasheh OPEN.

Run the golden-master test after any engine change so the controls and numbers stay trustworthy:
`./venv/bin/python tests/test_golden_master.py`.
