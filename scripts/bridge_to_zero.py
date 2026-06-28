#!/usr/bin/env python3
"""Bridge the P&L from booked net to corrected net (granular tool; close.py runs this in context).
Entity-type aware via --entity/--config (break_even plugs the under-recorded contribution;
for_profit removes only proven duplicates and never plugs). Examples:

  python bridge_to_zero.py --entity st_gertrude --pnl "P&L.csv" --duplicate 35617.29
  python bridge_to_zero.py --income 144864.90 --expenses 187191.54 --duplicate 35617.29
"""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(os.path.dirname(__file__), '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity')
    ap.add_argument('--income', type=float); ap.add_argument('--expenses', type=float)
    ap.add_argument('--pnl')
    ap.add_argument('--duplicate', '--phantom', type=float, required=True,
                    help='P&L-portion duplicate expense (phantom_charges_pl) from reconcile')
    a = ap.parse_args()

    funding_model, target = 'break_even', 0
    if a.entity:
        ent, _ = C.load_config(a.config, a.entity)
        funding_model = ent.get('funding_model', 'break_even')
        target = ent.get('net_income_target', 0)

    income, expenses = a.income, a.expenses
    if a.pnl:
        p = C.load_pnl(a.pnl)
        income = income or p.get('income'); expenses = expenses or p.get('expenses')

    b = C.bridge(income, expenses, a.duplicate, funding_model=funding_model, net_income_target=target)
    print(json.dumps(b, indent=2))
    if funding_model == 'break_even':
        print(f"\n  Booked net {b['booked_net']:,.2f}  ->  remove dup {b['duplicate_expense_removed']:,.2f}"
              f"  +  record contributions {b['owner_contrib_to_record']:,.2f}  =  CORRECTED NET {b['corrected_net']:,.2f}")
    else:
        print(f"\n  Booked net {b['booked_net']:,.2f}  ->  remove dup {b['duplicate_expense_removed']:,.2f}"
              f"  =  CORRECTED NET {b['corrected_net']:,.2f}  (real result — not plugged)")


if __name__ == '__main__':
    main()
