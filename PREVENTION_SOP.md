# Prevention SOP — stop creating the phantom

The reconciliation cleans up the mess; this prevents it. Share with the bookkeeper and the owners.
There are two halves: **bookkeeping process** (the four rules) and **credential / access controls**
(who can touch the file). Both failed historically — that is why the books were wrecked.

## Root cause

One real credit card was set up as TWO accounts in QuickBooks, and bank-feed charges were ALSO
entered manually into the second account (often re-keyed at EUR × FX amounts, so duplicates are not
penny-identical). Every duplicated charge double-counts an expense and inflates the card liability.
At St. Gertrude this produced a $47,213.18 year-end balance phantom and a fake $42,326.64 loss.

## The four rules (bookkeeping process)

1. **One QBO account per real card.** If a real account has two physical cards, it is still ONE QBO
   account. Never create a second account "linked to" the first.
2. **Bank feed only — never hand-enter a fed transaction.** Let the feed bring charges in; categorize
   them. Do not also type them in. Manual entry is reserved for things the feed cannot see.
3. **Reconcile to the statement every month.** Use QBO's Reconcile against the real statement balance.
   A clean monthly reconcile makes a phantom impossible to accumulate.
4. **Owner-paid expenses get BOTH sides.** When an owner pays a program cost personally, book the
   expense AND the matching contribution (or loan) the same month — never the expense alone. (Whether
   it is a contribution or a loan is a tax decision — flag it for the attorney; do not guess.)

## Credential / access controls (who can touch the file)

The four rules above assume the only people in the file are the people who should be there. They
weren't. Two failure modes, named so they are not repeated:

- **"Alison" logged in AS Kimberly** — shared credentials, no audit trail. Because the user field said
  "Kimberly", there was no way to tell Alison's entries from Kimberly's. **Shared logins destroy the
  audit trail and make tampering invisible.**
- **"Bobbie" (a prior accountant) forced reconciliations** — Opening Balance Equity plugs,
  prior-period adjustments, deleted accounts. A role-limited login and a reviewed close cadence would
  have caught these the month they happened instead of a year later.

Controls to enforce:

1. **Separate named logins for every person.** No one logs in as anyone else, ever. Each of Kimberly,
   Karen, the bookkeeper, and the tax advisor has their own login. This is the single most important
   control — it restores the audit trail.
2. **MFA required on every login.** No exceptions.
3. **Accountant access is role-limited.** Bookkeepers/accountants get the accountant role scoped to
   what they need — not full admin. No one but the owners can add/delete accounts or change company
   settings.
4. **NO shared logins.** If credentials were ever shared (the Alison situation), rotate them and
   re-issue named logins before the next close.
5. **Close the period after each close.** Set a closing date with a password in QBO so entries cannot
   be back-dated into a reconciled period without an explicit override (which the audit log records).
   Track closed periods in `config/entities.yaml` (`controls.closed_periods`).

Cross-check: because the shared-login history means "who entered this" cannot be trusted from the user
field alone, corroborate every login attribution against the QBO **Audit Log** export (see
`CONTROLS_SOP.md`).

## Detection — the monthly drift check

`drift_check.py` (scheduled task) compares each card's QBO balance to the latest real statement
balance and alerts if they diverge by more than `defaults.phantom_alert_threshold` (default $500 in
`config/entities.yaml`). Catch a small drift the month it appears instead of an annual five-figure
phantom.

Wiring:
- **What it reads:** the per-account `statement_balance_eoy` (or the month's `statement_balances.csv`)
  vs the QBO register's as-of balance — the same book-vs-real comparison the close uses, run monthly.
- **Threshold:** `defaults.phantom_alert_threshold` (USD). Tune per entity if needed.
- **Schedule:** run on a monthly cron after statements close (≈ the 5th). It is READ-ONLY like
  everything else — it alerts; it does not fix.
- **On alert:** open the offending account, find the divergence, and apply rule 2/3 (it is almost
  always a hand-entered fed transaction or a missed monthly reconcile).
