#!/usr/bin/env python3
"""duplicate_scan.py — generalize the duplicate / phantom detection to ALL accounts.

The St. Gertrude close exposed ONE root-cause pattern: a single real credit card was
recorded as TWO QBO accounts, with bank-feed charges manually re-keyed (at EUR x FX, so
NOT penny-identical) into the second account — inflating both the P&L expense and the
year-end liability. The Paleo redo has ~30 accounts and that pattern can recur on any of
them, declared or not.

This control does two things per entity:

  (a) DECLARED pairs: for every account flagged `qbo_recorded_as_two` with a `feed_card`
      and a `manual_dupe_card`, run C.pair_duplicates and report the tiered delete-set,
      the EXACT control total (book P&L charges - real charges), and coverage.

  (b) UNDECLARED pairs: across every register discovered for the entity, heuristically
      scan for two registers where a high fraction of one register's in-year MANUAL rows
      cluster onto another register's rows (same date+payee+near-amount logic as
      pair_duplicates). Those are flagged as a SUSPECTED 'same real card booked twice'
      even when the config has not declared them yet.

INVARIANTS honored here:
  * READ-ONLY. Produces a worksheet/finding only. A human (Leigh Ann) executes deletes.
  * Duplicates are FX-re-keyed -> matched by date+payee+near-amount, NEVER exact amount.
  * The duplicate TOTAL is exact; the per-line split is an ESTIMATE for review.
  * Do NOT bulk-delete the inflated account — part of it is the sole legit booking.
  * run() accepts **kw and NEVER raises; missing optional inputs degrade gracefully.

Controls contract (consumed by close.py -> controls['duplicate_scan']):
  { status: 'OK'|'FLAG'|'NOTE'|'ERROR',
    exceptions: [ ... per-finding dicts ... ],
    note: '<human one-liner>',
    per_account: [ ... phantom table rows ... ],
    suspected_duplicate_account_pairs: [ ... ] }
"""
import argparse, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common as C

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


# --------------------------------------------------------------------------- #
# File discovery (mirrors close.py: tolerant of space/underscore differences)
# --------------------------------------------------------------------------- #
def _flex(pattern):
    pats = {pattern}
    pats.add(pattern.replace(' ', '_'))
    pats.add(pattern.replace('_', ' '))
    pats.add(pattern.replace(' ', '*').replace('_', '*'))
    return pats


def _discover(period, patterns):
    found = []
    for pat in patterns or []:
        for p in _flex(pat):
            found += glob.glob(os.path.join(period, '**', p), recursive=True)
    return sorted(set(found))


def _first(period, patterns):
    f = _discover(period, patterns)
    return f[0] if f else None


# --------------------------------------------------------------------------- #
# Heuristic: cluster one register's MANUAL rows onto another register's rows.
# Reuses the SAME matching philosophy as C.pair_duplicates (date window + payee
# fuzzy + near-amount FX band) but without needing a declared feed/dupe mapping.
# --------------------------------------------------------------------------- #
def _amt(x):
    """Charge value as a clean float; NaN/blank -> 0.0 (note: `float(nan or 0)` is NaN, not 0)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v  # v != v is True only for NaN


def _manual_rows(df, year):
    iny = df[df['Date'].dt.year == year]
    man = iny[iny['AddedIn'].astype(str).str.contains('Manual', case=False, na=False)]
    return man.copy().reset_index(drop=True)


def _cluster_fraction(man, other_iny, dm):
    """What fraction of `man` (one register's in-year manual rows) finds a plausible
    twin in `other_iny` (another register's in-year rows)? Greedy 1:1 assignment so a
    single popular feed row cannot soak up many manual rows. Returns (matched, total,
    matched_book_total)."""
    win = dm.get('date_window_days', 8)
    apct = dm.get('amount_pct', 0.06)
    aabs = dm.get('amount_abs', 3.0)
    psim = dm.get('payee_min_similarity', 0.30)

    of = other_iny.reset_index(drop=True)
    cands = []
    for mi, r in man.iterrows():
        ca = _amt(r['Charge'])
        if ca == 0:
            continue
        for fi, f in of.iterrows():
            cf = _amt(f['Charge'])
            if cf == 0:
                continue
            dd = abs((r['Date'] - f['Date']).days)
            if dd > win:
                continue
            if abs(ca - cf) > max(aabs, apct * cf):
                continue
            ps = max(C._payee_sim(r['Payee'], f['Payee']), C._payee_sim(r['Memo'], f['Memo']),
                     C._payee_sim(r['Payee'], f['Memo']), C._payee_sim(r['Memo'], f['Payee']))
            if ps < psim:
                continue
            score = ps - 0.02 * dd - abs(ca - cf) / max(cf, 1)
            cands.append((score, mi, fi, ca, cf))
    cands.sort(reverse=True, key=lambda x: x[0])
    used_m, used_f, matched_total, n_nonexact = set(), set(), 0.0, 0
    for score, mi, fi, ca, cf in cands:
        if mi in used_m or fi in used_f:
            continue
        used_m.add(mi); used_f.add(fi); matched_total += ca
        if abs(ca - cf) > 0.02:   # FX-rekeyed duplicates differ; penny-identical = recurring charge, not a re-key
            n_nonexact += 1
    return len(used_m), int(len(man)), round(matched_total, 2), n_nonexact


def _account_label_for_register(ent, reg_basename):
    """Best-effort: which configured account does this register belong to (by its globs)?"""
    for acct in ent.get('accounts', []):
        for g in acct.get('qbo_register_globs', []):
            stem = g.replace('*', '').replace(' ', '').replace('_', '').lower()
            if stem and stem in reg_basename.replace(' ', '').replace('_', '').lower():
                return acct.get('label')
    return None


def scan_undeclared_pairs(ent, defaults, registers, year,
                          suspect_fraction=0.50, suspect_min_rows=8):
    """Across all discovered registers, flag pairs where a high fraction of one
    register's in-year manual rows cluster onto another register's in-year rows."""
    dm = (defaults or {}).get('duplicate_match', {}) or {}
    loaded = []
    for p in registers:
        try:
            df, _ = C.load_qbo_register(p)
        except Exception as e:
            loaded.append((p, None, f"load failed: {e}"))
            continue
        loaded.append((p, df, None))

    # Index in-year frames once.
    in_year = {}
    for p, df, err in loaded:
        if df is None:
            continue
        in_year[p] = df[df['Date'].dt.year == year].copy()

    suspects = []
    for pa, da, ea in loaded:
        if da is None:
            continue
        man = _manual_rows(da, year)
        if len(man) < suspect_min_rows:
            continue
        for pb, db, eb in loaded:
            if db is None or pb == pa:
                continue
            matched, total, mtot, n_nonexact = _cluster_fraction(man, in_year[pb], dm)
            if total == 0:
                continue
            frac = matched / total
            nonexact_frac = (n_nonexact / matched) if matched else 0.0
            # Require the cluster to be mostly FX-shifted (non-penny-identical). Two unrelated cards
            # that share recurring same-amount autopays (Netflix, utilities) would match exactly and
            # must NOT be flagged as 'same real card booked twice'.
            if frac >= suspect_fraction and matched >= suspect_min_rows and nonexact_frac >= 0.50:
                ba, bb = os.path.basename(pa), os.path.basename(pb)
                la = _account_label_for_register(ent, ba)
                lb = _account_label_for_register(ent, bb)
                already = bool(la and lb and la == lb)  # same configured account = already declared
                suspects.append({
                    'manual_register': ba,
                    'feed_register': bb,
                    'manual_account': la,
                    'feed_account': lb,
                    'manual_rows': total,
                    'clustered_onto_feed': matched,
                    'cluster_fraction': round(frac, 3),
                    'fx_shifted_fraction': round(nonexact_frac, 3),
                    'clustered_book_total': mtot,
                    'already_declared_same_account': already,
                    'finding': ('Same real card likely booked twice: a high fraction of '
                                f'{ba} manual rows match {bb} rows by date+payee+near-amount.'),
                })
    # De-dupe symmetric A->B / B->A: keep the higher-fraction direction per unordered pair.
    best = {}
    for s in suspects:
        key = tuple(sorted((s['manual_register'], s['feed_register'])))
        if key not in best or s['cluster_fraction'] > best[key]['cluster_fraction']:
            best[key] = s
    return list(best.values())


# --------------------------------------------------------------------------- #
# Declared-pair pairing per account (the defensible delete-set + control total)
# --------------------------------------------------------------------------- #
def scan_declared_account(acct, defaults, period, year):
    """For a `qbo_recorded_as_two` account: reconcile (for the EXACT control total)
    and pair duplicates. Returns a per-account phantom row dict."""
    dm = (defaults or {}).get('duplicate_match', {}) or {}
    label = acct.get('label', '?')
    regs = []
    for g in acct.get('qbo_register_globs', []):
        regs += _discover(period, [g])
    regs = sorted(set(regs))

    row = {'label': label, 'declared_pair': True,
           'registers': [os.path.basename(r) for r in regs]}

    feed = _first(period, [f"*{acct.get('feed_card')}*Register*.xls"])
    dupe = _first(period, [f"*{acct.get('manual_dupe_card')}*Register*.xls"])
    if not feed or not dupe:
        row['status'] = 'NOTE'
        row['note'] = (f"declared two-account pair but registers not both found "
                       f"(feed_card={acct.get('feed_card')}, manual_dupe_card={acct.get('manual_dupe_card')}) "
                       f"— export both QBO registers for this card into the period folder.")
        return row, None

    real_csv = _first(period, [acct['real_csv_glob']]) if acct.get('real_csv_glob') else None
    # EXACT control total: book P&L charges (in-year) - real charges (in-year).
    rec = C.reconcile_card(regs or [feed, dupe], real_csv, year,
                           real_schema=acct.get('csv_schema', 'capital_one'),
                           statement_balance_eoy=acct.get('statement_balance_eoy'),
                           carry_in_prior_year=acct.get('carry_in_prior_year', 0.0))
    control = rec['phantom_charges_pl']
    if real_csv is None:
        row['note_real_csv'] = ('no real transaction CSV found — control total uses book charges only; '
                                'export the bank/card transaction download to pin the exact phantom.')

    pairs, summ = C.pair_duplicates(
        dupe, feed, acct.get('feed_card'), year,
        date_window_days=dm.get('date_window_days', 8),
        amount_pct=dm.get('amount_pct', 0.06), amount_abs=dm.get('amount_abs', 3.0),
        payee_min_similarity=dm.get('payee_min_similarity', 0.30),
        control_total=control)

    row.update({
        'phantom_charges_pl': rec['phantom_charges_pl'],
        'phantom_charges_gross': rec['phantom_charges_gross'],
        'book_ending_balance': rec['book_ending_balance'],
        'balance_phantom': rec.get('balance_phantom'),
        'real_csv': os.path.basename(real_csv) if real_csv else None,
        'pairing': summ,
        'status': 'FLAG' if summ.get('matched_as_duplicate') else 'NOTE',
    })
    return row, pairs


# --------------------------------------------------------------------------- #
# Entry point (controls contract)
# --------------------------------------------------------------------------- #
def run(ent=None, defaults=None, period=None, year=None, **kw):
    """Controls entry point. NEVER raises; returns the status/exceptions/note contract."""
    out = {'control': 'duplicate_scan', 'status': 'OK', 'exceptions': [],
           'per_account': [], 'suspected_duplicate_account_pairs': [],
           'note': '', '_pairs_by_account': {}}
    try:
        if ent is None or period is None or year is None:
            out['status'] = 'NOTE'
            out['note'] = 'not provided — need ent + period + year to scan for duplicate accounts.'
            return out
        if pd is None:
            out['status'] = 'ERROR'
            out['note'] = 'pandas not available — run ./setup.sh.'
            return out

        defaults = defaults or {}
        accounts = ent.get('accounts', []) or []

        # (a) declared two-account pairs ------------------------------------ #
        for acct in accounts:
            if acct.get('belongs_to_org') is False or acct.get('status') == 'dormant':
                continue
            if acct.get('qbo_recorded_as_two') and acct.get('feed_card') and acct.get('manual_dupe_card'):
                try:
                    row, pairs = scan_declared_account(acct, defaults, period, year)
                except Exception as e:
                    row, pairs = ({'label': acct.get('label', '?'), 'declared_pair': True,
                                   'status': 'ERROR', 'note': f'scan failed: {e}'}, None)
                out['per_account'].append(row)
                if pairs is not None and not pairs.empty:
                    out['_pairs_by_account'][acct.get('label', '?')] = pairs
                if row.get('status') == 'FLAG':
                    out['exceptions'].append({
                        'kind': 'declared_duplicate_account',
                        'account': row['label'],
                        'phantom_charges_pl': row.get('phantom_charges_pl'),
                        'matched_as_duplicate': (row.get('pairing') or {}).get('matched_as_duplicate'),
                        'matched_duplicate_total': (row.get('pairing') or {}).get('matched_duplicate_total'),
                        'coverage_pct': (row.get('pairing') or {}).get('coverage_pct'),
                        'residual_unmatched_phantom': (row.get('pairing') or {}).get('residual_unmatched_phantom'),
                        'tax_position': False,
                        'note': ('Tiered delete-set is an ESTIMATE; control total is EXACT. '
                                 'Do NOT bulk-delete — part of this account is the sole legit booking. '
                                 'Human (Leigh Ann) executes deletions.'),
                    })

        # (b) undeclared suspected pairs ------------------------------------ #
        all_regs = _discover(period, ['*Register*.xls'])
        suspects = scan_undeclared_pairs(ent, defaults, all_regs, year)
        # Drop ones that simply re-state an already-declared pair (same configured account).
        novel = [s for s in suspects if not s.get('already_declared_same_account')]
        out['suspected_duplicate_account_pairs'] = suspects
        for s in novel:
            out['exceptions'].append({
                'kind': 'suspected_undeclared_duplicate_account',
                'manual_register': s['manual_register'],
                'feed_register': s['feed_register'],
                'cluster_fraction': s['cluster_fraction'],
                'clustered_book_total': s['clustered_book_total'],
                'tax_position': False,
                'note': (s['finding'] + ' NOT yet declared in config — review whether these two QBO '
                         'registers are the SAME real card. Flag only; do not delete.'),
            })

        # status + human note ----------------------------------------------- #
        n_decl = sum(1 for r in out['per_account'] if r.get('status') == 'FLAG')
        n_susp = len(novel)
        if n_decl or n_susp:
            out['status'] = 'FLAG'
        elif any(r.get('status') in ('NOTE', 'ERROR') for r in out['per_account']):
            out['status'] = 'NOTE'
        bits = []
        if n_decl:
            bits.append(f"{n_decl} declared duplicate-account pair(s) quantified")
        if n_susp:
            bits.append(f"{n_susp} suspected UNDECLARED duplicate-account pair(s)")
        if not bits:
            bits.append('no duplicate-account pattern detected')
        out['note'] = ('; '.join(bits) +
                       '. Totals are exact; per-line splits are ESTIMATES for human review (READ-ONLY).')
        return out
    except Exception as e:  # absolute backstop — a control must never crash the close
        return {'control': 'duplicate_scan', 'status': 'ERROR', 'exceptions': [],
                'per_account': [], 'suspected_duplicate_account_pairs': [],
                'note': f'duplicate_scan failed but did not raise: {e}'}


def _print_human(res):
    print(f"== duplicate_scan == status={res['status']}")
    print(f"   {res['note']}\n")
    print("Per-account phantom table:")
    if not res['per_account']:
        print("  (no declared two-account pairs configured)")
    for r in res['per_account']:
        if 'phantom_charges_pl' in r:
            pr = r.get('pairing') or {}
            bp = r.get('balance_phantom')
            print(f"  - {r['label']:<34} phantom(P&L) ${r['phantom_charges_pl']:>12,.2f}"
                  + (f"  balance phantom ${bp:>12,.2f}" if bp is not None else ""))
            print(f"      registers: {', '.join(r.get('registers') or [])}")
            print(f"      matched {pr.get('matched_as_duplicate')} of {pr.get('manual_entries')} manual "
                  f"= ${pr.get('matched_duplicate_total', 0):,.2f}  "
                  f"coverage {pr.get('coverage_pct')}%  control ${pr.get('control_total_phantom_pl', 0):,.2f}  "
                  f"residual ${pr.get('residual_unmatched_phantom', 0):,.2f}")
            print(f"      tiers: T1={pr.get('tier1')} (${pr.get('tier1_total', 0):,.2f})  "
                  f"T2={pr.get('tier2')}  T3={pr.get('tier3')}")
        else:
            print(f"  - {r['label']:<34} {r.get('status')}: {r.get('note', '')}")
    print("\nSuspected duplicate-account pairs (heuristic, undeclared-aware):")
    if not res['suspected_duplicate_account_pairs']:
        print("  (none)")
    for s in res['suspected_duplicate_account_pairs']:
        tag = 'ALREADY DECLARED' if s.get('already_declared_same_account') else 'UNDECLARED — REVIEW'
        print(f"  - [{tag}] {s['manual_register']} -> {s['feed_register']}  "
              f"frac={s['cluster_fraction']}  ({s['clustered_onto_feed']}/{s['manual_rows']} rows, "
              f"${s['clustered_book_total']:,.2f})")


def main():
    ap = argparse.ArgumentParser(description="Scan ALL configured accounts for duplicate-account phantoms (READ-ONLY).")
    ap.add_argument('--config', default=os.path.join(HERE, '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity', required=True)
    ap.add_argument('--period', required=True, help="folder holding this period's QBO exports")
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--out', help='optional: write the declared-pair tiered delete-set CSV(s) to this folder')
    ap.add_argument('--json', action='store_true', help='print the raw controls dict as JSON')
    a = ap.parse_args()

    ent, defaults = C.load_config(a.config, a.entity)
    res = run(ent=ent, defaults=defaults, period=a.period, year=a.year)

    if a.out and res.get('_pairs_by_account'):
        os.makedirs(a.out, exist_ok=True)
        for label, pairs in res['_pairs_by_account'].items():
            safe = ''.join(ch if ch.isalnum() else '_' for ch in label)
            fp = os.path.join(a.out, f'duplicates_{safe}.csv')
            pairs.drop(columns=[c for c in pairs.columns if c.startswith('__')]).to_csv(fp, index=False)
            print('wrote', fp)

    if a.json:
        printable = {k: v for k, v in res.items() if k != '_pairs_by_account'}
        print(json.dumps(printable, indent=2, default=str))
    else:
        _print_human(res)


if __name__ == '__main__':
    main()
