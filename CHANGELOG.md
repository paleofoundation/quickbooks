# Changelog

## v2.1 — adversarial-review hardening

Fixes from a multi-agent adversarial correctness review (hunting wrong-but-plausible outputs):
- **Balance Sheet read the PRIOR-YEAR column** on QBO's default Comparison export — every parsed
  balance was the wrong year. Now takes the current-year (first) column. This had falsely flagged a
  $9,049.96 "Personal expenses (deleted)" plug (actually $0 in 2025) and reported prior-year total
  assets to the 990 selector.
- **Off-period rows are no longer mislabeled as tampering.** A rolling export naturally contains
  prior/next-period rows; these are now INFORMATIONAL. Genuine back-dating is flagged only when a row
  is dated into a configured *closed* period (and corroborated via the Audit Log). The findings memo's
  A4 section dropped from ~260 noisy bullets to the handful of genuine plugs.
- **FBAR peak balance is computed per year** (was one peak over the whole file, attributed to every
  year — a false "FBAR required" for sub-$10k years).
- **Duplicate-account heuristic** now requires the cluster to be mostly FX-shifted (non-penny-identical),
  so two unrelated cards sharing recurring same-amount autopays aren't flagged as one card booked twice.
- **Credit-card sign handling** in `reconcile_card` is now account-kind aware (only abs() a credit card;
  surface an unexpected positive balance instead of silently masking a sign error).
- **audit_log** no longer mistakes the payee/Name column for the User column, matches expected users by
  whole name-token (not short substring), and its CLI loads entity controls.
- **verify.py** added genuinely independent ties (bridge inputs == P&L totals; corrected figures foot).
- Docs/deps: removed the unused `pdfplumber` dependency, corrected the workbook tab names, dropped the
  non-working `pytest` command. Discovery no longer re-ingests a prior run's output folder.

## v2 — config-driven, forensic, multi-entity

v2 turns a set of per-account scripts into one config-driven, entity-type-aware close with a
forensic-controls layer, while keeping the hard invariant that everything is **READ-ONLY**.

### Added
- **One command, config-driven.** `./close.sh --entity <e> --year <y> --period "<folder>"` runs the
  whole pipeline from `config/entities.yaml`. Add an account to the YAML — no code change. (v1 was a
  sequence of `ingest → reconcile_cards → bridge_to_zero → verify → build_package` invocations with
  hand-passed `--income/--expenses/--register` flags.)
- **Balance phantom** (NEW). Reconciliation now reports BOTH the charge-flow phantom (duplicate
  expense to remove) AND the balance phantom (book ending balance − real statement balance),
  decomposed into `carry_in` (prior year) + `added_in_year`. This is what surfaces the headline
  $47,213.18 St. Gertrude liability overstatement vs the $35,617.29 P&L duplicate.
- **Real duplicate pairing** (NEW). `pair_duplicates` replaces v1's `find_duplicates`: global
  best-score-first matching of manual re-keyed entries to their bank-feed originals by date + fuzzy
  payee/memo + FX-tolerant near-amount, tiered (1–3), with an EXACT control total and a residual that
  is "investigate", not "delete". (v1 exact-amount logic found ~$4,651; v2 recovers $20K+.)
- **Entity-type-aware bridge.** `funding_model: break_even` plugs the under-recorded contribution to
  the target; `funding_model: for_profit` removes only proven duplicates and NEVER plugs — the
  residual is the real net. A negative implied contribution now FAILS verify with "this entity is not
  break-even".
- **Forensic-controls layer.** `audit_log`, `forced_reconciliation_check`, `duplicate_scan`,
  `compliance_checks`, `form990_selector` — each runs if installed and can NEVER crash the close. See
  `CONTROLS_SOP.md`. Surfaces shared-login/back-dating, Opening Balance Equity / prior-period plugs,
  and the duplicate set.
- **Compliance flags (mechanical, deferred).** Gross-receipts / total-assets on corrected figures,
  FBAR balance trigger, Schedule L drift, reasonable-comp red flag — all flagged "for Anasheh, status
  OPEN", never decided. New DEFERRAL MATRIX in `SKILL.md`.
- **Monthly drift check.** `drift_check.py` compares each card's QBO balance to the latest real
  statement and alerts over `phantom_alert_threshold`. Wiring documented in `PREVENTION_SOP.md`.
- **Package + memo.** `build_package` + `genmemo` produce the close workbook and a findings memo split
  into bookkeeper fixes vs tax items.
- **Golden-master test.** `tests/test_golden_master.py` runs the real engine on checked-in 2025
  St. Gertrude fixtures and asserts the hand-verified close to the cent (phantom $35,617.29, balance
  phantom $47,213.18, owner contribution $6,709.35, corrected net $0) plus the for-profit no-plug
  safety case.
- **Paleo profile.** `certified_paleo` (S-corp, `for_profit`, EIN 47-5610964, realm
  123145847686732) seeded in `entities.yaml` with ~30 accounts and the attorney flag list, for the
  2025/2026 redo.
- **New docs.** `CONTROLS_SOP.md` (forensic-controls layer + segregation-of-duties/close-cadence) and
  this `CHANGELOG.md`. `INVARIANTS` block added to the top of `SKILL.md`.

### Changed
- `config/entities.yaml` is now the single source of truth (funding model, accounts, funders,
  compliance profile, controls/expected-users). Statement balances captured in a
  `statement_balances.csv` per period because PDFs don't parse reliably (`FOLDER_STANDARD.md`).
- `verify.py` is entity-type-aware: break_even must tie to target with no negative plug; for_profit
  must not invent a contribution and reports per-account phantoms.
- Loaders hardened and grounded in real export formats: QBO register `.xls`, Capital One / Chase /
  Wells Fargo (headerless) / Amex / PayPal / Bank of Cyprus (EUR) CSVs, Apple `.numbers`, with a
  SHA-256 input manifest pinned into `close_summary.json` for reproducibility.

### Invariants (unchanged, now stated up front)
READ-ONLY; flag-but-never-decide tax positions; FX-tolerant matching (never exact); never bulk-delete
the inflated account; duplicate total exact / per-line estimate; owner contribution flagged, not
classified.

---

## v1 — the original St. Gertrude close

Per-account scripts (`ingest`, `reconcile_cards`, `find_duplicates`, `bridge_to_zero`, `verify`,
`build_package`) run by hand with explicit flags. Reconciled book vs real for one Capital One account,
found duplicates on exact-amount logic, and bridged the P&L to $0. Established the folder standard and
the four prevention rules.
