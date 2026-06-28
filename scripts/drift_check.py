#!/usr/bin/env python3
"""drift_check.py — the EARLY-DETECTION monthly tripwire that stops a phantom before it grows.

For every configured account that has a `statement_balance_eoy`, compare the QBO
register's own as-of running balance (the BOOK liability) against the real bank/card
statement balance (the TRUTH). Any |drift| over `defaults['phantom_alert_threshold']`
trips a FAIL and a NONZERO exit so a scheduler can alert before the next month
compounds the error.

This is the lightweight monthly sibling of reconcile_cards.py: it does not need the
real transaction CSV, only the QBO register export and the known statement balance.
Where a statement balance is NOT configured for an account, it is reported as
'needs statement balance' (NOT silently passed) so the gap is visible.

INVARIANT: READ-ONLY. This reads QBO register exports + the config. It never edits
QuickBooks, never calls any QBO write/import/update tool, and produces no mutations —
a human (Leigh Ann) reconciles/consolidates from what this surfaces.

Why it catches the St. Gertrude phantom: ONE real Capital One card was booked as TWO
QBO accounts, so the book liability runs ~$60,086.95 against a real statement balance
of $12,873.77 — a drift of ~$47,213 that this check fails loudly on, pre-cleanup.

--------------------------------------------------------------------------------
WIRING IT TO A MONTHLY SCHEDULED TASK (READ-ONLY — no QBO writes):

  1. Each month, export the current QBO register .xls for every card into the
     period folder (same exports close.py consumes). This is the ONLY manual step
     and it is a READ/export from QBO, never a write.

  2. Run the check (exits nonzero on any over-threshold drift):

       /path/to/venv/bin/python scripts/drift_check.py \
           --config config/entities.yaml \
           --entity st_gertrude --period "/path/to/2025 close folder" \
           --year 2025 --month 6

  3. Schedule it. Example cron (1st of each month, 7am), emailing only on FAIL —
     cron mails stdout when the command exits nonzero:

       0 7 1 * * cd /path/to/repo && \
         ./venv/bin/python scripts/drift_check.py --config config/entities.yaml \
           --entity st_gertrude --period "/path/to/period" --year 2025 \
           || echo "DRIFT ALERT — see output above" | mail -s "Books drift" you@org

     (launchd / Task Scheduler / a CI cron job work the same way: nonzero exit =
     alert. Nothing here logs into or mutates QuickBooks.)
--------------------------------------------------------------------------------
"""
import argparse, glob, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common as C


def _flex(pattern):
    """Tolerate space/underscore differences in real filenames (mirrors close.py)."""
    pats = {pattern}
    pats.add(pattern.replace(' ', '_'))
    pats.add(pattern.replace('_', ' '))
    pats.add(pattern.replace(' ', '*').replace('_', '*'))
    return pats


def discover(period, patterns):
    found = []
    for pat in patterns:
        for p in _flex(pat):
            found += glob.glob(os.path.join(period, '**', p), recursive=True)
    return sorted(set(found))


def _is_credit_card(acct):
    """Card/credit accounts store the running balance as a negative liability; compare magnitudes.
    Treat anything that isn't an explicitly-flagged bank/depository account as a card."""
    kind = str(acct.get('account_type', acct.get('kind', 'credit_card'))).lower()
    return 'bank' not in kind and 'checking' not in kind and 'savings' not in kind and 'deposit' not in kind


def check_account(acct, period, year, threshold):
    """Compute book vs real drift for ONE account. Never raises — returns a result dict."""
    label = acct.get('label', '?')
    res = {'label': label}

    stmt = acct.get('statement_balance_eoy')
    if stmt is None:
        res['status'] = 'NEEDS STATEMENT BALANCE'
        res['note'] = 'no statement_balance_eoy configured — cannot drift-check; export the statement and add it to entities.yaml'
        return res

    # Account is off-org / dormant: still report, but note it.
    if acct.get('belongs_to_org') is False:
        res['status'] = 'SKIP'
        res['note'] = 'not an org account (owner personal) — drift not applicable'
        return res

    globs = acct.get('qbo_register_globs', [])
    if not globs and acct.get('real_cards'):
        globs = [f"*{c}*Register*.xls" for c in acct['real_cards']]
    regs = []
    for g in globs:
        regs += discover(period, [g])
    regs = sorted(set(regs))
    if not regs:
        res['status'] = 'NO REGISTER'
        res['note'] = 'no QBO register export found in period — export it from QBO (read-only) and re-run'
        return res

    book = 0.0
    used = []
    for p in regs:
        try:
            df, _hdr = C.load_qbo_register(p)
            book += C.register_asof_balance(df, year)
            used.append(os.path.basename(p))
        except Exception as e:  # a single bad export must not crash the monthly run
            res.setdefault('warnings', []).append(f"{os.path.basename(p)}: {e}")

    cc = _is_credit_card(acct)
    book_bal = round(abs(book), 2) if cc else round(book, 2)
    real_bal = round(float(stmt), 2)
    drift = round(book_bal - real_bal, 2)
    res.update({
        'status': 'PASS' if abs(drift) <= threshold else 'FAIL',
        'qbo_balance': book_bal,
        'real_balance': real_bal,
        'drift': drift,
        'threshold': threshold,
        'registers': used,
        'is_credit_card': cc,
    })
    return res


def run(ent, defaults, period, year, **kw):
    """Run the monthly drift check over every account in `ent`. NEVER raises.

    Returns a dict: {'results': [...per-account...], 'fails': int, 'unchecked': int,
    'breached': bool, 'exit_code': int, 'threshold': float, 'month': <month or None>}.
    `exit_code` is nonzero iff any account drift exceeds the threshold.
    """
    try:
        threshold = float((defaults or {}).get('phantom_alert_threshold', 500))
    except Exception:
        threshold = 500.0
    month = kw.get('month')

    results = []
    for acct in (ent or {}).get('accounts', []):
        try:
            results.append(check_account(acct, period, year, threshold))
        except Exception as e:  # belt-and-suspenders: run() must never raise
            results.append({'label': acct.get('label', '?'), 'status': 'ERROR', 'note': str(e)})

    fails = sum(1 for r in results if r.get('status') == 'FAIL')
    unchecked = sum(1 for r in results
                    if r.get('status') in ('NEEDS STATEMENT BALANCE', 'NO REGISTER', 'ERROR'))
    return {
        'entity': (ent or {}).get('legal_name'),
        'year': year, 'month': month, 'threshold': threshold,
        'results': results, 'fails': fails, 'unchecked': unchecked,
        'breached': fails > 0, 'exit_code': 1 if fails > 0 else 0,
    }


def _print_table(report):
    label = report.get('entity') or '?'
    when = f"{report['year']}" + (f"-{int(report['month']):02d}" if report.get('month') else "")
    print(f"== Drift check: {label} — {when} ==")
    print(f"   alert threshold: ${report['threshold']:,.2f}  (book QBO balance vs real statement balance)\n")

    hdr = f"  {'Account':<34}{'QBO bal':>15}{'Real bal':>15}{'Drift':>15}   Status"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in report['results']:
        st = r.get('status', '?')
        if st in ('PASS', 'FAIL'):
            print(f"  {r['label']:<34}{r['qbo_balance']:>15,.2f}{r['real_balance']:>15,.2f}"
                  f"{r['drift']:>15,.2f}   {st}")
        else:
            print(f"  {r['label']:<34}{'—':>15}{'—':>15}{'—':>15}   {st}"
                  + (f" ({r['note']})" if r.get('note') else ""))
        for w in r.get('warnings', []):
            print(f"      !! {w}")

    print()
    if report['breached']:
        print(f"  RESULT: FAIL — {report['fails']} account(s) over the ${report['threshold']:,.2f} threshold. "
              "Investigate BEFORE next month compounds it (likely a duplicate-account / phantom).")
    else:
        print("  RESULT: PASS — no account drift over threshold.")
    if report['unchecked']:
        print(f"  NOTE: {report['unchecked']} account(s) could not be checked "
              "(needs statement balance / no register / error) — not counted as PASS.")
    print("\n  READ-ONLY: no QuickBooks data was modified. Reconcile from these figures by hand (Leigh Ann).")


def main():
    ap = argparse.ArgumentParser(description="Monthly book-vs-statement drift tripwire (READ-ONLY).")
    ap.add_argument('--config', default=os.path.join(HERE, '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity', required=True)
    ap.add_argument('--period', required=True, help="folder holding this period's QBO register exports")
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--month', type=int, default=None, help='optional month label (1-12) for the report header')
    a = ap.parse_args()

    ent, defaults = C.load_config(a.config, a.entity)
    report = run(ent, defaults, a.period, a.year, month=a.month)
    _print_table(report)
    sys.exit(report['exit_code'])


if __name__ == '__main__':
    main()
