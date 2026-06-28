#!/usr/bin/env python3
"""Book-vs-real card reconciliation for ONE account (granular tool; close.py does all accounts).
Example:
  python reconcile_cards.py --register "5391 Register.xls" --register "7933 Register.xls" \
      --real-csv "CapitalOne.csv" --year 2025 --schema capital_one \
      --statement-balance 12873.77 --carry-in 7344.61 \
      --feed-card 7933 --dupe-register "5391 Register.xls" --out duplicates.csv
"""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--register', action='append', required=True, help='QBO register .xls (repeatable)')
    ap.add_argument('--real-csv'); ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--schema', default='capital_one')
    ap.add_argument('--statement-balance', type=float)
    ap.add_argument('--carry-in', type=float, default=0.0)
    ap.add_argument('--feed-card'); ap.add_argument('--dupe-register'); ap.add_argument('--out')
    a = ap.parse_args()

    res = C.reconcile_card(a.register, a.real_csv, a.year, real_schema=a.schema,
                           statement_balance_eoy=a.statement_balance, carry_in_prior_year=a.carry_in)
    print(json.dumps(res, indent=2))

    if a.dupe_register and a.feed_card:
        feed = next((r for r in a.register if a.feed_card in os.path.basename(r)), a.register[-1])
        pairs, summ = C.pair_duplicates(a.dupe_register, feed, a.feed_card, a.year,
                                        control_total=res['phantom_charges_pl'])
        print("\nDuplicate pairing:")
        print(json.dumps(summ, indent=2))
        if a.out:
            pairs.drop(columns=[c for c in pairs.columns if c.startswith('__')]).to_csv(a.out, index=False)
            print("wrote", a.out)


if __name__ == '__main__':
    main()
