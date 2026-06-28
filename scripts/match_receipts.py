#!/usr/bin/env python3
"""Match vendor receipts to real card charges (and optionally flag QBO double-bookings).
Usage: python match_receipts.py --receipts-csv receipts.csv --card-csv personal_card.csv --out match.csv
receipts.csv needs columns: order,date,amount (USD). card-csv is a bank/card transaction download."""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__)); import _common as C
import pandas as pd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--receipts-csv', required=True); ap.add_argument('--card-csv', required=True)
    ap.add_argument('--amount-col', default='amount'); ap.add_argument('--tol', type=float, default=0.50)
    ap.add_argument('--out')
    a = ap.parse_args()
    rec = pd.read_csv(a.receipts_csv); card = C.load_real_card(a.card_csv)
    charges = card['_debit'].round(2).tolist()
    out = []
    for _, r in rec.iterrows():
        amt = round(C.to_num(r[a.amount_col]), 2)
        hit = any(abs(amt - c) <= a.tol for c in charges)
        out.append({**r.to_dict(), 'matched_card_charge': 'YES' if hit else 'NO'})
    res = pd.DataFrame(out)
    n = (res['matched_card_charge'] == 'YES').sum()
    print(f"Receipts: {len(res)} | matched to a real card charge: {n} | unmatched: {len(res)-n}")
    if a.out: res.to_csv(a.out, index=False); print("wrote", a.out)
if __name__ == '__main__': main()
