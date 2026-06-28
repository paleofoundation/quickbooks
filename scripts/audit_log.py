#!/usr/bin/env python3
"""audit_log.py — forensic parse of a QBO Audit Log export.

Export from QBO:  Settings (gear) > Audit Log > Filter as needed > Export (CSV/Excel).

The shared-login history (Alison logged in AS Kimberly) means the *user* field on a
transaction cannot be trusted on its own — the Audit Log is the only place the real
event trail survives. This module flags and tabulates:

  (a) after-the-fact edits — an event whose change time is materially after the
      transaction Date, or after the period close;
  (b) entries made under a shared/flagged login — cross-referenced against
      ent['controls']['known_unauthorized'] and ['expected_users'];
  (c) Deleted / Voided transactions inside the period;
  (d) entries dated INTO a closed period from ent['controls']['closed_periods'] (back-dating);
  (e) bulk same-second / same-minute mass edits (a scripted plug, not human data entry).

INVARIANT: READ-ONLY. Produces a worksheet of exceptions for a human (Leigh Ann); never
edits QuickBooks, never decides a tax position. run() never raises. If no audit-log file
is present, returns status 'not_run' with exact export instructions (correct, not a failure).
"""
import os, sys, glob, re, datetime as _dt
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common as C
import pandas as pd

CHECK = "audit_log"

# Filename patterns QBO uses (and common renames) for an audit-log export.
_GLOBS = ('*Audit*Log*.csv', '*Audit*Log*.xlsx', '*Audit*Log*.xls',
          '*audit_log*.csv', '*audit_log*.xlsx', '*audit*log*.csv',
          '*AuditTrail*.csv', '*audit-log*.csv')

# Column-header aliases (QBO has varied these across exports/locales).
_COL = {
    'changed': ('date changed', 'last modified', 'changed', 'change date', 'event date', 'date/time', 'date'),
    'user':    ('user', 'username', 'changed by', 'modified by', 'last modified by', 'edited by'),
    'event':   ('event', 'action', 'activity', 'change type'),
    'txn_type':('transaction type', 'type', 'object'),
    'txn_date':('transaction date', 'txn date', 'date of transaction', 'original date'),
    'name':    ('name', 'payee', 'vendor', 'customer'),
    'amount':  ('amount', 'total'),
}
# Event words that mean a record was removed/blanked.
_DELETE_WORDS = ('delete', 'void')
# Event words that mean an existing record was changed after creation.
_EDIT_WORDS = ('edit', 'modif', 'change', 'updat')


def _name_tokens(s):
    import re as _re
    return {t for t in _re.split(r'[^a-z0-9]+', str(s).lower()) if len(t) >= 3}


def _user_is_expected(user_lower, expected_lower_list):
    """True only if the login shares a whole name-token (>=3 chars) with an expected user.
    Token-exact, never raw substring — so a short/spoofed fragment like 'kim' is NOT auto-trusted."""
    ut = _name_tokens(user_lower)
    if not ut:
        return False
    for e in expected_lower_list:
        if ut & _name_tokens(e):
            return True
    return False


def _discover(period):
    found = []
    for g in _GLOBS:
        found += glob.glob(os.path.join(period, '**', g), recursive=True)
    return sorted(set(found))


def _read_any(path):
    if path.lower().endswith(('.xlsx', '.xls')):
        return pd.read_excel(path)
    # QBO audit-log CSVs sometimes carry a title/preamble before the header row.
    raw = open(path, encoding='utf-8', errors='replace').read().splitlines()
    hdr = 0
    for i, line in enumerate(raw[:25]):
        low = line.lower()
        if ('date' in low or 'changed' in low) and ('user' in low or 'event' in low or 'action' in low):
            hdr = i
            break
    return pd.read_csv(path, skiprows=hdr)


def _pick(cols_lower, aliases):
    for a in aliases:
        if a in cols_lower:
            return cols_lower[a]
    # loose contains-match as a fallback
    for key, orig in cols_lower.items():
        if any(a in key for a in aliases):
            return orig
    return None


def _parse(df):
    """Map an arbitrary QBO audit-log frame to canonical columns; returns a normalized frame."""
    cols_lower = {str(c).lower().strip(): c for c in df.columns}
    pick = {k: _pick(cols_lower, al) for k, al in _COL.items()}
    out = pd.DataFrame()
    out['_changed'] = pd.to_datetime(df[pick['changed']], errors='coerce') if pick['changed'] else pd.NaT
    out['_user'] = df[pick['user']].astype(str) if pick['user'] else ''
    out['_event'] = df[pick['event']].astype(str) if pick['event'] else ''
    out['_txn_type'] = df[pick['txn_type']].astype(str) if pick['txn_type'] else ''
    out['_txn_date'] = pd.to_datetime(df[pick['txn_date']], errors='coerce') if pick['txn_date'] else pd.NaT
    out['_name'] = df[pick['name']].astype(str) if pick['name'] else ''
    out['_amount'] = df[pick['amount']].apply(C.to_num) if pick['amount'] else 0.0
    return out, pick


def _closed_cutoffs(ent):
    """Parse ent['controls']['closed_periods'] (list of 'YYYY-12-31') into Timestamps."""
    cps = ((ent or {}).get('controls') or {}).get('closed_periods') or []
    out = []
    for c in cps:
        try:
            out.append(pd.Timestamp(str(c)))
        except Exception:
            continue
    return out


def _flagged_users(ent):
    """Lowercased name fragments of known-unauthorized actors (e.g. 'alison')."""
    known = ((ent or {}).get('controls') or {}).get('known_unauthorized') or []
    frags = []
    for k in known:
        # config stores a sentence; first token before '(' is the actor name.
        name = re.split(r'[\(\-]', str(k))[0].strip()
        if name:
            frags.append(name.lower())
    return frags


def run(ent, defaults, period, year, pnl=None, pnl_path=None, bs_path=None,
        accounts=None, bridge=None, **kw):
    files = _discover(period)
    if not files:
        return {'check': CHECK, 'status': 'not_run', 'exceptions': [],
                'counts': {'audit_log_files': 0}, 'dollars': {},
                'note': ("No QBO Audit Log export found in the period. To enable this check, in "
                         "QuickBooks Online go to Settings (gear) > Audit Log, set the date filter to "
                         f"cover {year} (and a buffer into {year + 1} to catch after-the-fact edits), "
                         "click Export, and drop the CSV/Excel into the period folder named like "
                         "'Audit Log.csv'. Then re-run the close. Without it, who-entered-what cannot be "
                         "corroborated against the shared-login history — run forced_reconciliation_check "
                         "in the meantime for the audit-log-free footprints.")}

    expected = [u.lower() for u in (((ent or {}).get('controls') or {}).get('expected_users') or [])]
    flagged = _flagged_users(ent)
    closed = _closed_cutoffs(ent)
    period_end = pd.Timestamp(year, 12, 31)
    # "materially after" the transaction date threshold (a same-week correction is normal).
    edit_lag_days = (defaults or {}).get('audit_edit_lag_days', 2)

    exceptions = []
    n_afterfact = n_shared = n_deleted = n_backdate = 0
    dollars_afterfact = dollars_deleted = dollars_backdate = 0.0
    parse_warn = []

    for path in files:
        src = os.path.basename(path)
        try:
            raw = _read_any(path)
            rows, pick = _parse(raw)
        except Exception as e:
            parse_warn.append(f"{src}: {e}")
            continue

        for i, r in rows.iterrows():
            ev = str(r['_event']).lower()
            user = str(r['_user']).strip()
            ul = user.lower()
            changed = r['_changed']
            txn_date = r['_txn_date']
            amt = round(float(r['_amount'] or 0), 2)
            base = {'source': src, 'changed': changed.isoformat() if pd.notna(changed) else None,
                    'user': user, 'event': str(r['_event']), 'txn_type': str(r['_txn_type']),
                    'txn_date': txn_date.date().isoformat() if pd.notna(txn_date) else None,
                    'name': str(r['_name']), 'amount': amt}

            # (b) shared / flagged-login activity
            if flagged and any(f in ul for f in flagged):
                n_shared += 1
                exceptions.append({**base, 'kind': 'flagged_login',
                                   'why': 'entry made under a known-unauthorized/shared login'})
            elif expected and ul and not _user_is_expected(ul, expected):
                n_shared += 1
                exceptions.append({**base, 'kind': 'unexpected_user',
                                   'why': 'entry made by a user not on the expected-users list'})

            # (c) deletions / voids inside the period
            if any(w in ev for w in _DELETE_WORDS):
                in_period = (pd.isna(txn_date) or txn_date.year == year)
                if in_period:
                    n_deleted += 1
                    dollars_deleted += abs(amt)
                    exceptions.append({**base, 'kind': 'deleted_or_voided',
                                       'why': f'{r["_event"]} of a transaction in/around the period'})

            # (a) after-the-fact edit: change time materially after txn date or after close
            if any(w in ev for w in _EDIT_WORDS) and pd.notna(changed):
                lag = None
                if pd.notna(txn_date):
                    lag = (changed.normalize() - txn_date.normalize()).days
                if (lag is not None and lag > edit_lag_days) or (changed > period_end and (pd.isna(txn_date) or txn_date.year <= year)):
                    n_afterfact += 1
                    dollars_afterfact += abs(amt)
                    exceptions.append({**base, 'kind': 'after_the_fact_edit',
                                       'edit_lag_days': (int(lag) if lag is not None else None),
                                       'why': 'record changed materially after its transaction date / after period close'})

            # (d) back-dating INTO a closed period
            if pd.notna(txn_date):
                for cutoff in closed:
                    if txn_date <= cutoff and pd.notna(changed) and changed > cutoff:
                        n_backdate += 1
                        dollars_backdate += abs(amt)
                        exceptions.append({**base, 'kind': 'backdated_into_closed_period',
                                           'closed_through': cutoff.date().isoformat(),
                                           'why': 'transaction dated into a CLOSED period but entered/changed after close'})
                        break

    # (e) bulk same-second / same-minute mass edits (scripted plug, not human entry)
    bucket_sec = defaultdict(list)
    bucket_min = defaultdict(list)
    for e in exceptions:
        pass  # buckets are built from raw rows below for completeness
    # rebuild from all parsed rows so bulk detection isn't limited to already-flagged ones
    all_changed = []
    for path in files:
        try:
            rows, _ = _parse(_read_any(path))
        except Exception:
            continue
        for _, r in rows.iterrows():
            if pd.notna(r['_changed']):
                all_changed.append((r['_changed'], str(r['_user'])))
    for ts, user in all_changed:
        bucket_sec[(ts.replace(microsecond=0), user)].append(ts)
        bucket_min[(ts.replace(second=0, microsecond=0), user)].append(ts)
    n_bulk = 0
    SEC_THRESHOLD, MIN_THRESHOLD = 5, 25
    for (ts, user), hits in bucket_sec.items():
        if len(hits) >= SEC_THRESHOLD:
            n_bulk += 1
            exceptions.append({'kind': 'bulk_same_second_edit', 'changed': ts.isoformat(),
                               'user': user, 'event_count': len(hits),
                               'why': f'{len(hits)} changes in the SAME SECOND — scripted/bulk edit, not hand entry'})
    flagged_seconds = {(ts, user) for (ts, user), h in bucket_sec.items() if len(h) >= SEC_THRESHOLD}
    for (ts, user), hits in bucket_min.items():
        if len(hits) >= MIN_THRESHOLD and not any(s[0].replace(second=0) == ts and s[1] == user for s in flagged_seconds):
            n_bulk += 1
            exceptions.append({'kind': 'bulk_same_minute_edit', 'changed': ts.isoformat(),
                               'user': user, 'event_count': len(hits),
                               'why': f'{len(hits)} changes in the SAME MINUTE — likely a mass/forced edit'})

    counts = {'audit_log_files': len(files), 'after_the_fact_edits': n_afterfact,
              'flagged_or_unexpected_user': n_shared, 'deleted_or_voided': n_deleted,
              'backdated_into_closed_period': n_backdate, 'bulk_mass_edits': n_bulk,
              'total_exceptions': len(exceptions)}
    dollars = {'after_the_fact_edit_exposure': round(dollars_afterfact, 2),
               'deleted_or_voided_exposure': round(dollars_deleted, 2),
               'backdated_into_closed_period_exposure': round(dollars_backdate, 2)}

    status = 'exceptions' if exceptions else 'clean'
    note = ("Audit-log forensics complete. Each exception is for Leigh Ann to corroborate (the "
            "shared-login history means the user field alone is not proof). 'after_the_fact_edit' "
            "and 'flagged_login' rows are the Alison-as-Kimberly tell; deletions/voids and "
            "closed-period back-dating are the Bobbie tell.") if exceptions else \
           "Audit log parsed; no after-the-fact edits, flagged logins, deletions, or back-dating found."
    if not closed:
        note += " (Note: controls.closed_periods is empty — back-dating-into-closed-period detection is inactive; add 'YYYY-12-31' once a year is locked.)"
    if parse_warn:
        note += " | parse warnings: " + " ; ".join(parse_warn)
    return {'check': CHECK, 'status': status, 'exceptions': exceptions,
            'counts': counts, 'dollars': dollars, 'note': note}


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser(description="Forensic parse of a QBO Audit Log export (READ-ONLY).")
    ap.add_argument('--config', default=os.path.join(os.path.dirname(__file__), '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity', help='entity key in entities.yaml (loads its controls: expected/known-unauthorized users, closed periods)')
    ap.add_argument('--period', required=True)
    ap.add_argument('--year', type=int, required=True)
    a = ap.parse_args()
    ent, defaults = ({'controls': {}}, {})
    if a.entity:
        ent, defaults = C.load_config(a.config, a.entity)
    res = run(ent=ent, defaults=defaults, period=a.period, year=a.year)
    print(json.dumps(res, indent=2, default=str))
