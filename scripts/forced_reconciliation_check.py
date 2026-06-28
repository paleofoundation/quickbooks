#!/usr/bin/env python3
"""forced_reconciliation_check.py — detect Bobbie-style forced/garbage reconciliations
WITHOUT an audit log, from the Balance Sheet + the QBO registers in the period.

A "forced reconciliation" is when a prior accountant makes the books *appear* to tie out
by plugging the difference into an equity/clearing account instead of finding the real
error. The footprints are visible on the face of the statements:

  (a) nonzero 'Opening balance equity', 'Prior period adjustments',
      'Personal expenses (deleted)' on the Balance Sheet (the classic plugs);
  (b) any 'Reconciliation Discrepancies' / 'Reconciliation Adjustment' account activity;
  (c) round-number adjusting entries in the registers (Charge/Payment an exact multiple of
      100/500/1000 with Type/Memo hinting Journal/Adjustment/Opening Balance);
  (d) register rows still 'Cleared' (C) or blank at period end rather than 'Reconciled' (R);
  (e) the back-dated register rows (recomputed via C.split_period).

INVARIANT: READ-ONLY. This produces a worksheet of EXCEPTIONS for a human (Leigh Ann) to
work; it never edits QuickBooks and never decides a tax position. run() never raises.
"""
import os, sys, glob, re
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common as C

CHECK = "forced_reconciliation_check"


def _num(x):
    """NaN-safe float coercion (pandas NaN is truthy, so `x or 0` does not catch it)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v  # NaN != NaN

# Balance-sheet labels that are plug/clearing accounts: a nonzero balance is a red flag.
_BS_PLUG_LABELS = ('opening balance equity', 'prior period adjustment', 'prior-period adjustment',
                   'personal expenses (deleted)', 'reconciliation discrepanc',
                   'reconciliation adjustment', 'ask my accountant', 'uncategorized')
# Register account/memo hints that a row is itself an adjusting/clearing entry.
_RECON_HINTS = ('reconciliation discrepanc', 'reconciliation adjustment', 'opening balance')
# Memo/Type tokens that hint a manual adjusting journal entry.
_JOURNAL_HINTS = ('journal', 'adjust', 'opening balance', 'reconcil', 'plug', 'transfer to balance')
# Round-number magnitudes that smell like a manual plug rather than a real receipt.
_ROUND_STEPS = (1000, 500, 100)


def _round_step(amount):
    """Return the largest round step an amount is an exact non-trivial multiple of, else None."""
    a = abs(round(float(amount), 2))
    if a < 100:
        return None
    for step in _ROUND_STEPS:
        if abs(a % step) < 0.005:
            return step
    return None


def _norm_rec(status):
    """Normalize RecStatus to one of R / C / blank. Handles both word and letter codes."""
    s = str(status).strip().lower()
    if s in ('', 'nan', 'none'):
        return ''
    if s.startswith('r'):   # 'Reconciled' / 'R'
        return 'R'
    if s.startswith('c'):   # 'Cleared' / 'C'
        return 'C'
    return s.upper()[:1]


def _find_registers(period, accounts):
    """Discover register .xls paths in the period (prefer config globs, else generic)."""
    pats = set()
    for acct in (accounts or []):
        for g in acct.get('qbo_register_globs', []) or []:
            pats.add(g)
    if not pats:
        pats = {'*Register*.xls', '*register*.xls'}
    found = []
    for pat in pats:
        found += glob.glob(os.path.join(period, '**', pat), recursive=True)
        found += glob.glob(os.path.join(period, '**', pat.replace(' ', '_')), recursive=True)
    return sorted(set(found))


def run(ent, defaults, period, year, pnl=None, pnl_path=None, bs_path=None,
        accounts=None, bridge=None, **kw):
    exceptions, dollars = [], {}
    notes = []

    # ----- (a)/(b) Balance-sheet plug & clearing accounts -------------------- #
    bs_flag_total = 0.0
    if bs_path and os.path.exists(bs_path):
        try:
            bs = C.load_balance_sheet(bs_path)
        except Exception as e:
            bs = None
            notes.append(f"could not parse balance sheet: {e}")
        if bs is not None:
            # _control_flags already surfaces OBE/Reconciliation Discrepancies via _common.
            for f in bs.get('_control_flags', []):
                exceptions.append({'kind': 'bs_control_account', 'account': f['account'],
                                   'amount': round(f['amount'], 2),
                                   'why': 'forced-reconciliation plug/clearing account carries a nonzero balance'})
                bs_flag_total += abs(f['amount'])
            # Plus the explicit Bobbie footprints not always in CONTROL_FLAG_ACCOUNTS.
            seen = {e['account'].lower() for e in exceptions if e['kind'] == 'bs_control_account'}
            for label, val in bs.items():
                if not isinstance(val, (int, float)) or label.startswith('_') or label.startswith('__'):
                    continue
                low = label.lower()
                if low in seen:
                    continue
                if any(h in low for h in _BS_PLUG_LABELS) and abs(val) > 0.005:
                    exceptions.append({'kind': 'bs_plug_account', 'account': label,
                                       'amount': round(val, 2),
                                       'why': 'Bobbie-style plug/deleted-account footprint on the Balance Sheet'})
                    bs_flag_total += abs(val)
    else:
        notes.append("no Balance Sheet CSV in period — export Reports > Balance Sheet (Comparison, accrual) "
                     "to detect Opening Balance Equity / Prior Period Adjustment plugs.")

    # ----- (c)/(d)/(e) Register-level forensics ------------------------------ #
    regs = _find_registers(period, accounts)
    # Years explicitly marked closed in config — only a row dated INTO a closed period is a
    # genuine review item. A rolling export naturally carries prior/next-period rows; those are
    # informational, NOT a forced-reconciliation footprint, and are NOT added to the exception list.
    closed_years = set()
    for cp in ((ent.get('controls') or {}).get('closed_periods') or []):
        m = re.match(r'(\d{4})', str(cp))
        if m:
            closed_years.add(int(m.group(1)))

    round_total = closed_backdate_total = unreconciled_total = recon_acct_total = 0.0
    n_round = n_priorperiod = n_future = n_closed_backdate = n_unrec = n_recon_acct = 0
    rows_scanned = 0

    for path in regs:
        try:
            df, _hdr = C.load_qbo_register(path)
        except Exception as e:
            notes.append(f"could not read register {os.path.basename(path)}: {e}")
            continue
        src = os.path.basename(path)
        iny, off = C.split_period(df, year)
        rows_scanned += len(df)

        # (e) off-period rows. Informational counts unless a row is dated into a CLOSED period
        # (the only case that is a genuine back-dating review item without an audit log).
        for _, r in off.iterrows():
            yr = int(r['Date'].year)
            amt = round(_num(r['Charge']) - _num(r['Payment']), 2)
            if yr < year:
                n_priorperiod += 1
            else:
                n_future += 1
            if yr in closed_years:
                n_closed_backdate += 1
                closed_backdate_total += abs(amt)
                exceptions.append({'kind': 'backdated_into_closed_period', 'source': src, 'row': int(r['__row']),
                                   'date': r['Date'].date().isoformat(), 'payee': str(r['Payee']),
                                   'amount': amt, 'account': str(r['Account']),
                                   'why': f'dated into CLOSED period {yr} but present in a later register — review the entry date in the Audit Log'})

        for _, r in df.iterrows():
            charge, payment = _num(r['Charge']), _num(r['Payment'])
            memo, typ, acct = str(r['Memo']), str(r['Type']), str(r['Account'])
            blob = f"{memo} {typ} {acct}".lower()

            # (b) reconciliation-discrepancy / opening-balance account activity
            if any(h in acct.lower() or h in memo.lower() for h in _RECON_HINTS):
                amt = round(charge - payment, 2)
                n_recon_acct += 1
                recon_acct_total += abs(amt)
                exceptions.append({'kind': 'reconciliation_account_activity', 'source': src,
                                   'row': int(r['__row']), 'date': r['Date'].date().isoformat(),
                                   'amount': amt, 'account': acct, 'memo': memo[:80],
                                   'why': 'activity in a reconciliation/opening-balance clearing account'})

            # (c) round-number adjusting entries (only when there is a journal/adjustment hint)
            for col, amount in (('Charge', charge), ('Payment', payment)):
                if amount == 0:
                    continue
                step = _round_step(amount)
                journalish = any(h in blob for h in _JOURNAL_HINTS)
                if step is not None and journalish:
                    n_round += 1
                    round_total += abs(amount)
                    exceptions.append({'kind': 'round_number_adjustment', 'source': src,
                                       'row': int(r['__row']), 'date': r['Date'].date().isoformat(),
                                       'payee': str(r['Payee']), 'column': col,
                                       'amount': round(amount, 2), 'round_to': step,
                                       'type': typ, 'account': acct,
                                       'why': f'exact multiple of {step} with a journal/adjustment hint'})
                    break  # one flag per row

            # (d) in-period rows not Reconciled (R) at period end
            if r['Date'].year == year:
                code = _norm_rec(r['RecStatus'])
                if code != 'R':
                    n_unrec += 1
                    unreconciled_total += abs(charge - payment)

    # (d) rolled up as a single summary exception (per-row would flood the worksheet).
    if n_unrec:
        exceptions.append({'kind': 'unreconciled_at_period_end', 'count': n_unrec,
                           'dollars': round(unreconciled_total, 2),
                           'why': f"{n_unrec} in-{year} rows are blank/Cleared, not Reconciled (R) — "
                                  "the reconciliation was never truly completed/locked"})

    dollars = {
        'bs_plug_exposure': round(bs_flag_total, 2),
        'reconciliation_account_activity': round(recon_acct_total, 2),
        'round_number_adjustments': round(round_total, 2),
        'backdated_into_closed_period_exposure': round(closed_backdate_total, 2),
        'unreconciled_at_period_end': round(unreconciled_total, 2),
    }
    counts = {
        'bs_control_accounts': sum(1 for e in exceptions if e['kind'] in ('bs_control_account', 'bs_plug_account')),
        'reconciliation_account_rows': n_recon_acct,
        'round_number_adjustments': n_round,
        'backdated_into_closed_period_rows': n_closed_backdate,
        'unreconciled_rows': n_unrec,
        'registers_scanned': len(regs),
        'register_rows_scanned': rows_scanned,
        # informational only — a rolling export normally contains these; not tampering by themselves:
        'info_priorperiod_rows': n_priorperiod,
        'info_future_dated_rows': n_future,
    }

    if not regs and not (bs_path and os.path.exists(bs_path)):
        return {'check': CHECK, 'status': 'not_run', 'exceptions': [], 'counts': counts,
                'dollars': dollars,
                'note': "No Balance Sheet CSV and no QBO register .xls found in the period. Export "
                        "Reports > Balance Sheet (Comparison) and the credit-card registers, then re-run."}

    status = 'exceptions' if exceptions else 'clean'
    note = ("Forced-reconciliation footprints found. These are the tells of a plugged/garbage "
            "reconciliation — each row is for Leigh Ann to investigate; do NOT auto-reverse. "
            "Dollar figures are exposure, not a correcting entry.") if exceptions else \
           "No forced-reconciliation footprints detected in the Balance Sheet or registers."
    if n_priorperiod or n_future:
        note += (f" | INFORMATIONAL: {n_priorperiod} prior-period and {n_future} future-dated rows are present "
                 "in the rolling register export — normal for a mid-cycle export, NOT tampering by themselves. "
                 "Genuine back-dating is only flagged when a row is dated into a configured CLOSED period "
                 "(none configured here) — confirm entry dates in the QBO Audit Log via audit_log.py.")
    if notes:
        note += " | " + " ; ".join(notes)
    return {'check': CHECK, 'status': status, 'exceptions': exceptions,
            'counts': counts, 'dollars': dollars, 'note': note}


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser(description="Detect forced/garbage reconciliations (READ-ONLY).")
    ap.add_argument('--period', required=True)
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--bs', help='path to the Balance Sheet CSV')
    a = ap.parse_args()
    bs = a.bs or (sorted(glob.glob(os.path.join(a.period, '**', '*Balance Sheet*.csv'), recursive=True)) or [None])[0]
    res = run(ent={}, defaults={}, period=a.period, year=a.year, bs_path=bs, accounts=None)
    print(json.dumps(res, indent=2, default=str))
