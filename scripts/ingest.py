#!/usr/bin/env python3
"""Discover and inventory the period folder per FOLDER_STANDARD. Reports what's present/missing
so you know you have everything before reconciling. Usage: python ingest.py --root "<folder>"."""
import argparse, glob, os, sys
PATTERNS = {
    'QBO registers (.xls)':      ['**/*Register*.xls', '**/*register*.xls'],
    'Real card/bank CSVs':       ['**/*transaction*download*.csv', '**/*Activity*.CSV', '**/*Activity*.csv'],
    'QBO P&L CSV':               ['**/*Profit and Loss*.csv', '**/*Profit_and_Loss*.csv'],
    'QBO Balance Sheet CSV':     ['**/*Balance Sheet*.csv'],
    'Owner bank CSVs (BOC etc.)':['**/BOC*/**/*.csv', '**/*TransactionHistory*.csv'],
    'Statement PDFs':            ['**/Statement*.pdf', '**/*Statement*.pdf'],
    'Receipts (PDF)':            ['**/Receipts/**/*.pdf'],
}
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--root', required=True); ap.add_argument('--config')
    a = ap.parse_args()
    print(f"Inventory of: {a.root}\n" + "="*60)
    for label, pats in PATTERNS.items():
        found = []
        for p in pats: found += glob.glob(os.path.join(a.root, p), recursive=True)
        found = sorted(set(found))
        flag = "OK " if found else "!! MISSING"
        print(f"[{flag}] {label}: {len(found)}")
        for f in found[:8]: print("        - " + os.path.relpath(f, a.root))
        if len(found) > 8: print(f"        ... +{len(found)-8} more")
    print("="*60 + "\nReview MISSING items against FOLDER_STANDARD.md before reconciling.")
if __name__ == '__main__': main()
