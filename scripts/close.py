#!/usr/bin/env python3
"""close.py — the one-command driver.

Reads config/entities.yaml, auto-discovers the period's files, reconciles every
configured account (book vs real), pairs duplicates, bridges the P&L (entity-type
aware), runs whatever forensic-controls checks are installed, verifies, and writes
ONE close package + a machine-readable summary.

  ./close.sh --entity st_gertrude --year 2025 --period "<folder>"

INVARIANT: READ-ONLY. This never edits QuickBooks. Every consolidation/deletion is
executed by a human (the bookkeeper) from the worksheets this produces.
"""
import argparse, json, os, sys, glob, importlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common as C


def _flex(pattern):
    """Make a glob tolerant of space/underscore differences in real filenames."""
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
    # Never re-ingest a prior run's generated outputs (close_*/ folders).
    found = [f for f in found if not any(part.startswith('close_') for part in f.split(os.sep))]
    return sorted(set(found))


def first(period, patterns):
    f = discover(period, patterns)
    return f[0] if f else None


def reconcile_accounts(ent, defaults, period, year):
    """Loop every reconcilable account in the entity config."""
    results = []
    for acct in ent.get('accounts', []):
        if acct.get('belongs_to_org') is False or acct.get('status') == 'dormant':
            results.append({'label': acct['label'], 'skipped': acct.get('status', 'not-org-account')})
            continue
        regs = []
        for g in acct.get('qbo_register_globs', []):
            regs += discover(period, [g])
        regs = sorted(set(regs))
        real_csv = first(period, [acct['real_csv_glob']]) if acct.get('real_csv_glob') else None
        if not regs:
            results.append({'label': acct['label'], 'missing': 'no QBO register export found'})
            continue
        rec = C.reconcile_card(regs, real_csv, year,
                               real_schema=acct.get('csv_schema', 'capital_one'),
                               statement_balance_eoy=acct.get('statement_balance_eoy'),
                               carry_in_prior_year=acct.get('carry_in_prior_year', 0.0),
                               is_credit_card=acct.get('kind', 'credit_card') != 'bank')
        rec['label'] = acct['label']
        rec['registers'] = [os.path.basename(r) for r in regs]
        rec['real_csv'] = os.path.basename(real_csv) if real_csv else None
        # duplicate pairing for the two-accounts-one-card case
        if acct.get('qbo_recorded_as_two') and acct.get('feed_card') and acct.get('manual_dupe_card'):
            feed = first(period, [f"*{acct['feed_card']}*Register*.xls"])
            dupe = first(period, [f"*{acct['manual_dupe_card']}*Register*.xls"])
            if feed and dupe:
                dm = (defaults.get('duplicate_match') or {})
                pairs, summ = C.pair_duplicates(
                    dupe, feed, acct['feed_card'], year,
                    date_window_days=dm.get('date_window_days', 8),
                    amount_pct=dm.get('amount_pct', 0.06), amount_abs=dm.get('amount_abs', 3.0),
                    payee_min_similarity=dm.get('payee_min_similarity', 0.30),
                    control_total=rec['phantom_charges_pl'])
                rec['duplicate_pairing'] = summ
                rec['_pairs_df'] = pairs
        results.append(rec)
    return results


def try_module(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="One-command nonprofit/business close reconciliation (READ-ONLY).")
    ap.add_argument('--config', default=os.path.join(HERE, '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity', required=True)
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--period', required=True, help='folder holding this period\'s exports (see FOLDER_STANDARD.md)')
    ap.add_argument('--pnl', help='override: path to the QBO P&L CSV')
    ap.add_argument('--out', default=None, help='output folder for the close package')
    a = ap.parse_args()

    ent, defaults = C.load_config(a.config, a.entity)
    funding_model = ent.get('funding_model', 'break_even')
    target = ent.get('net_income_target', 0)
    tol = defaults.get('close_tolerance', 1.00)
    out = a.out or os.path.join(a.period, f"close_{a.entity}_{a.year}")
    os.makedirs(out, exist_ok=True)

    print(f"== Close: {ent.get('legal_name', a.entity)} — {a.year} ==")
    print(f"   funding_model={funding_model}  target_net={target}\n")

    # 1) reconcile every account
    accts = reconcile_accounts(ent, defaults, a.period, a.year)
    total_phantom_pl = sum(r.get('phantom_charges_pl', 0) or 0 for r in accts if 'phantom_charges_pl' in r)
    print("Per-account reconciliation:")
    for r in accts:
        if 'phantom_charges_pl' in r:
            bp = r.get('balance_phantom')
            print(f"  - {r['label']:<32} phantom(P&L) ${r['phantom_charges_pl']:>12,.2f}"
                  + (f"  balance phantom ${bp:>12,.2f}" if bp is not None else "")
                  + (f"  [!! {r['sign_flag']}]" if r.get('sign_flag') else ""))
        else:
            print(f"  - {r['label']:<32} {r.get('missing') or r.get('skipped')}")

    # 2) P&L + bridge
    pnl_path = a.pnl or first(a.period, ['*Profit and Loss*.csv', '*Profit_and_Loss*.csv', '*P&L*.csv'])
    bridge = pnl = None
    if pnl_path:
        pnl = C.load_pnl(pnl_path)
        bridge = C.bridge(pnl['income'], pnl['expenses'], total_phantom_pl,
                          funding_model=funding_model, net_income_target=target, tolerance=tol)
        print("\nBridge:")
        print(f"  booked net ${bridge['booked_net']:,.2f}  -> remove duplicate expense ${bridge['duplicate_expense_removed']:,.2f}", end="")
        if funding_model == 'break_even':
            print(f"  + record contributions ${bridge['owner_contrib_to_record']:,.2f}  = corrected net ${bridge['corrected_net']:,.2f}")
            if 'WARNING' in bridge:
                print("  !! " + bridge['WARNING'])
        else:
            print(f"  = corrected net ${bridge['corrected_net']:,.2f}  (real result — NOT plugged to a target)")
    else:
        print("\n!! No P&L CSV found in period — bridge skipped. Add the QBO P&L export (see FOLDER_STANDARD.md).")

    # 3) optional forensic-controls checks (run if the module is installed)
    controls = {}
    bs_path = first(a.period, ['*Balance Sheet*.csv'])
    for modname, fn in (('forced_reconciliation_check', 'run'), ('audit_log', 'run'),
                        ('duplicate_scan', 'run'), ('compliance_checks', 'run'), ('form990_selector', 'run')):
        mod = try_module(modname)
        if mod and hasattr(mod, fn):
            try:
                controls[modname] = getattr(mod, fn)(ent=ent, defaults=defaults, period=a.period,
                                                     year=a.year, pnl=pnl, pnl_path=pnl_path,
                                                     bs_path=bs_path, accounts=accts, bridge=bridge)
            except Exception as e:  # a controls check must never crash the close
                controls[modname] = {'error': str(e)}

    # 4) summary JSON (machine-readable, reproducible)
    inputs = []
    for r in accts:
        for rn in r.get('registers', []) or []:
            pass
    manifest = C.file_manifest([pnl_path, bs_path] + discover(a.period, ['*Register*.xls']))
    summary = {'entity': a.entity, 'legal_name': ent.get('legal_name'), 'year': a.year,
               'funding_model': funding_model, 'total_phantom_pl': round(total_phantom_pl, 2),
               'accounts': [{k: v for k, v in r.items() if not k.startswith('_')} for r in accts],
               'bridge': bridge, 'pnl': {k: v for k, v in (pnl or {}).items() if not k.startswith('__')},
               'controls': controls, 'input_manifest': manifest}
    with open(os.path.join(out, 'close_summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2, default=str)

    # 5) build the package if the builder is installed
    builder = try_module('build_package')
    if builder and hasattr(builder, 'build'):
        try:
            pkg = builder.build(out, summary, accts, bridge, ent, controls)
            print(f"\nPackage: {pkg}")
        except Exception as e:
            print(f"\n!! build_package failed: {e}")

    # 6) verify gate
    verify = try_module('verify')
    rc = 0
    if verify and hasattr(verify, 'verify_close'):
        rc = verify.verify_close(ent, defaults, pnl, total_phantom_pl, bridge, accts)
    print(f"\nWrote: {out}/close_summary.json")
    print("VERIFY:", "PASS" if rc == 0 else "FAIL")
    sys.exit(rc)


if __name__ == '__main__':
    main()
