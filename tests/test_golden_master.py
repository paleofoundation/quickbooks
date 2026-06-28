#!/usr/bin/env python3
"""Golden-master regression test — the accuracy guarantee.

Runs the real reconciliation engine on the checked-in 2025 Gardens of St. Gertrude
data (tests/fixtures/) and asserts it reproduces the hand-verified forensic close
to the cent. If a refactor breaks any number, this test fails loudly.

Run:  ./venv/bin/python tests/test_golden_master.py    (or: pytest tests/)

NOTE: the fixtures are REAL financial data. This repo is a private local tool —
do not push it to a public remote without scrubbing tests/fixtures/.
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
import _common as C

FIX = os.path.join(HERE, 'fixtures', 'st_gertrude_2025')
REG_5391 = os.path.join(FIX, '5391_Register.xls')
REG_7933 = os.path.join(FIX, '7933_Register.xls')
REAL_CSV = os.path.join(FIX, 'Capital One 7933 2025 transaction_download.csv')
PNL = os.path.join(FIX, 'Gardens of St. Gertrude Inc._Profit and Loss.csv')

# The hand-verified golden master (Gardens of St. Gertrude 2025).
GM = {
    'phantom_charges_pl': 35617.29,       # duplicate EXPENSE to remove from the P&L (exact)
    'phantom_charges_gross': 35714.65,
    'book_ending_balance': 60086.95,      # QBO book card liability at 12/31/2025
    'real_statement_balance': 12873.77,   # real Capital One statement balance
    'balance_phantom': 47213.18,          # the headline liability overstatement
    'balance_phantom_carry_in': 7344.61,  # carried in from 2024
    'balance_phantom_added_in_year': 39868.57,
    'owner_contrib_to_record': 6709.35,   # under-recorded owner in-kind contribution
    'corrected_net': 0.00,                # break-even target
    'booked_net': -42326.64,
}
TOL = 0.01


def _check(name, actual, expected, tol=TOL):
    ok = actual is not None and abs(actual - expected) <= tol
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {actual} (expected {expected})")
    return ok


def run():
    failures = 0

    # The fixtures are REAL financial data and are kept LOCAL only (gitignored, scrubbed from
    # history) — see tests/fixtures/README.md. A fresh clone won't have them, so skip cleanly.
    if not (os.path.exists(REG_5391) and os.path.exists(REAL_CSV) and os.path.exists(PNL)):
        print("GOLDEN MASTER: SKIPPED — local fixtures not present (see tests/fixtures/README.md).")
        return 0

    rec = C.reconcile_card([REG_5391, REG_7933], REAL_CSV, 2025,
                           real_schema='capital_one',
                           statement_balance_eoy=GM['real_statement_balance'],
                           carry_in_prior_year=GM['balance_phantom_carry_in'])
    pnl = C.load_pnl(PNL)
    b = C.bridge(pnl['income'], pnl['expenses'], rec['phantom_charges_pl'],
                 funding_model='break_even', net_income_target=0)

    print("Golden-master checks (Gardens of St. Gertrude 2025):")
    for k in ('phantom_charges_pl', 'phantom_charges_gross', 'book_ending_balance',
              'real_statement_balance', 'balance_phantom',
              'balance_phantom_carry_in', 'balance_phantom_added_in_year'):
        failures += not _check(k, rec.get(k), GM[k])
    failures += not _check('booked_net', b['booked_net'], GM['booked_net'])
    failures += not _check('owner_contrib_to_record', b['owner_contrib_to_record'], GM['owner_contrib_to_record'])
    failures += not _check('corrected_net', b['corrected_net'], GM['corrected_net'])

    # Pairing must produce a credible, exact-control-total-checked delete-set
    pairs, summ = C.pair_duplicates(REG_5391, REG_7933, '7933', 2025,
                                    date_window_days=10, control_total=rec['phantom_charges_pl'])
    print("\nDuplicate pairing:")
    print(f"  matched={summ['matched_as_duplicate']} ${summ['matched_duplicate_total']:,.2f} "
          f"coverage={summ['coverage_pct']}% control=${summ['control_total_phantom_pl']:,.2f}")
    failures += not (summ['matched_duplicate_total'] > 15000)  # >> the v1 find_duplicates $4,651
    failures += not (abs(summ['control_total_phantom_pl'] - GM['phantom_charges_pl']) <= TOL)

    # Entity-type-aware bridge MUST NOT plug a contribution for a for-profit.
    print("\nFor-profit safety (must NOT invent a contribution):")
    bf = C.bridge(200000, 250000, 30000, funding_model='for_profit', net_income_target=None)
    no_plug = 'owner_contrib_to_record' not in bf and bf['corrected_net'] == -20000.0
    print(f"  [{'PASS' if no_plug else 'FAIL'}] for_profit corrected_net={bf['corrected_net']}, no owner_contrib plug")
    failures += not no_plug

    print()
    if failures:
        print(f"GOLDEN MASTER: {failures} FAILURE(S)")
        return 1
    print("GOLDEN MASTER: ALL PASS")
    return 0


def test_golden_master():   # pytest entry point
    assert run() == 0


if __name__ == '__main__':
    sys.exit(run())
