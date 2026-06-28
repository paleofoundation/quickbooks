#!/usr/bin/env python3
"""verify.py — the REQUIRED accuracy gate. Nonzero exit on failure; never ship without a clean verify.

Independently re-derives the headline numbers and asserts internal consistency,
entity-type-aware:
  * break_even : the bridge must tie to the net-income target, the implied owner
                 contribution must NOT be negative (a negative plug means the entity
                 is not break-even), and the P&L must cross-foot.
  * for_profit : the P&L must cross-foot, the bridge must NOT have invented a
                 contribution, and per-account phantoms must be reported (the close is
                 'verified' on internal consistency + controls, NOT on net == a target).

Importable as verify_close(...) (used by close.py) or runnable standalone.
"""
import argparse, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common as C


def verify_close(ent, defaults, pnl, total_phantom_pl, bridge, accounts):
    funding_model = ent.get('funding_model', 'break_even')
    target = ent.get('net_income_target', 0)
    tol = defaults.get('close_tolerance', 1.00)
    fails = []
    print("\n-- verify --")

    if pnl is None or bridge is None:
        print("  [FAIL] no P&L / bridge available to verify")
        return 1

    # Independent re-derivation of the bridge from P&L + summed phantom
    reb = C.bridge(pnl['income'], pnl['expenses'], total_phantom_pl,
                   funding_model=funding_model, net_income_target=target, tolerance=tol)
    if abs(reb['corrected_net'] - bridge['corrected_net']) > tol:
        fails.append(f"bridge not reproducible: {reb['corrected_net']} vs {bridge['corrected_net']}")

    # Independent ties (not a re-run of the same call):
    #  (a) the bridge inputs must equal the P&L's own income/expenses totals
    if abs(bridge.get('booked_income', 0) - round(pnl['income'], 2)) > tol:
        fails.append(f"bridge income {bridge.get('booked_income')} != P&L income {round(pnl['income'],2)}")
    if abs(bridge.get('booked_expenses', 0) - round(pnl['expenses'], 2)) > tol:
        fails.append(f"bridge expenses {bridge.get('booked_expenses')} != P&L expenses {round(pnl['expenses'],2)}")
    #  (b) the corrected figures must actually foot to the corrected net
    foot = round(bridge.get('corrected_income', 0) - bridge.get('corrected_expenses', 0), 2)
    if abs(foot - bridge.get('corrected_net', 0)) > tol:
        fails.append(f"corrected income−expenses {foot} does not foot to corrected net {bridge.get('corrected_net')}")

    if pnl.get('_crossfoot_ok') is False:
        fails.append(f"P&L does not cross-foot (calc {pnl.get('_crossfoot_net_calc')} vs net {pnl.get('net')})")
    elif pnl.get('_crossfoot_ok') is None:
        print("  WARN:", pnl.get('_crossfoot_warning', "P&L net line absent — cross-foot unverified"))

    if funding_model == 'break_even':
        if not reb.get('ties_to_target'):
            fails.append(f"corrected net {reb['corrected_net']} != target {target} (tol {tol})")
        if reb.get('owner_contrib_to_record', 0) < -tol:
            fails.append(f"NEGATIVE implied contribution {reb['owner_contrib_to_record']} — entity is not break-even")
        print(f"  corrected net ${reb['corrected_net']:,.2f} (target {target})  "
              f"owner contrib to record ${reb.get('owner_contrib_to_record', 0):,.2f}")
    else:
        if 'owner_contrib_to_record' in bridge:
            fails.append("for_profit bridge invented a contribution plug — must not happen")
        print(f"  corrected net ${reb['corrected_net']:,.2f} (real result; per-account phantoms must clear to 0 after cleanup)")

    prior = sum(r.get('off_period_priorperiod_rows', 0) or 0 for r in accounts)
    future = sum(r.get('off_period_future_rows', 0) or 0 for r in accounts)
    if prior or future:
        print(f"  NOTE: {prior} prior-period and {future} future-dated register row(s) present (rolling export — "
              "informational, not tampering by themselves; true back-dating is flagged by audit_log).")
    for r in accounts:
        if r.get('sign_flag'):
            print(f"  [FLAG] {r.get('label','account')}: {r['sign_flag']}")

    if fails:
        for f in fails:
            print("  [FAIL]", f)
        print("  VERIFY: FAIL")
        return 1
    print("  VERIFY: PASS")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(HERE, '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity', required=True)
    ap.add_argument('--pnl', required=True)
    ap.add_argument('--duplicate', type=float, required=True, help='summed phantom_charges_pl from reconcile')
    a = ap.parse_args()
    ent, defaults = C.load_config(a.config, a.entity)
    pnl = C.load_pnl(a.pnl)
    bridge = C.bridge(pnl['income'], pnl['expenses'], a.duplicate,
                      funding_model=ent.get('funding_model', 'break_even'),
                      net_income_target=ent.get('net_income_target', 0))
    sys.exit(verify_close(ent, defaults, pnl, a.duplicate, bridge, []))


if __name__ == '__main__':
    main()
