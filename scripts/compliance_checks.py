#!/usr/bin/env python3
"""compliance_checks.py — regime-aware compliance FLAGS (flag-but-never-decide).

This module READS the close artifacts (config, P&L, bridge, Bank-of-Cyprus CSVs)
and emits a list of OPEN questions routed to the tax attorney (Anasheh). It NEVER
decides a tax position, never edits QuickBooks, and never raises out of run().

Every emitted item is a dict with the same shape:
  {item, amount_or_fact, question_for_attorney, regulatory_hook, status: 'OPEN'}

Dispatch is on ent['compliance_profile']['regime']:
  - us_501c3  : public-support-test sensitivity, Schedule L related-party,
                contemporaneous-acknowledgment, private-benefit/inurement.
  - us_s_corp : reasonable officer comp, FEIE-on-K-1, basis, charitable
                carryforwards, Schedule L drift (surfaces tax_context flags).
  - BOTH (if compliance_profile.foreign_accounts): FBAR/FinCEN-114 tripwire,
                discovered from BOC CSVs in the period.
"""
import os, glob
import datetime as _dt

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
import _common as C

# Rough EUR->USD used only to decide whether the FBAR $10k aggregate tripwire
# is PLAUSIBLY crossed. This is a screening rate, never a booked conversion;
# the actual Treasury year-end rate is for Anasheh to apply.
_EUR_USD_SCREEN = 1.10
_FBAR_THRESHOLD_USD = 10000.0
_ACK_THRESHOLD = 250.0


def _flag(item, amount_or_fact, question, hook):
    return {'item': item, 'amount_or_fact': amount_or_fact,
            'question_for_attorney': question, 'regulatory_hook': hook, 'status': 'OPEN'}


# --------------------------------------------------------------------------- #
# us_501c3
# --------------------------------------------------------------------------- #
def _checks_501c3(ent, bridge):
    flags = []
    cp = ent.get('compliance_profile', {}) or {}

    # CORRECTED figures (never booked) per the config basis.
    corrected_income = (bridge or {}).get('corrected_income')
    owner_contrib = (bridge or {}).get('owner_contrib_to_record')

    # Owner in-kind treatment (contribution vs loan) is undecided in config.
    owner_funders = [f for f in ent.get('funders', []) if f.get('role') == 'owner_in_kind']
    treatments = sorted({str(f.get('treatment', 'undecided')) for f in owner_funders})

    # 1) Public-support-test sensitivity. A single large related-party gift booked
    #    as a CONTRIBUTION counts toward total support but is limited (2% of total
    #    support, or excluded as a "disqualified person" gift) in the public-support
    #    fraction — it can tip the 33.3%/10%-facts-and-circumstances math.
    if corrected_income is not None:
        contrib_fact = (f"corrected gross receipts ${corrected_income:,.2f}; "
                        f"owner in-kind to record ${owner_contrib:,.2f}" if owner_contrib is not None
                        else f"corrected gross receipts ${corrected_income:,.2f}")
        flags.append(_flag(
            'Public-support-test sensitivity (owner in-kind as contribution vs loan)',
            contrib_fact,
            "Does treating the owner in-kind as a CONTRIBUTION (vs a loan) tip the public-support "
            "fraction below 33.3% (or 10% facts-and-circumstances)? Owners are disqualified persons; "
            "a single large related-party gift is limited in the public-support numerator.",
            "IRC 509(a)(1)/170(b)(1)(A)(vi) public-support test; Schedule A, Part II/III"))

    # 2) Schedule L related-party disclosure — only if the chosen treatment is a LOAN.
    if any('loan' in t.lower() for t in treatments):
        flags.append(_flag(
            'Schedule L related-party loan disclosure',
            f"owner in-kind treatment includes loan ({', '.join(treatments)})",
            "If the owner in-kind is treated as a LOAN, it is a loan to/from an interested person "
            "requiring Schedule L disclosure (and terms/interest review). Confirm reporting.",
            "Form 990/990-EZ Schedule L, Part II (interested-person transactions)"))
    else:
        # Treatment still undecided — surface it so the decision is made on the record.
        flags.append(_flag(
            'Owner in-kind treatment undecided (contribution vs loan)',
            f"treatment(s) on file: {', '.join(treatments) or 'undecided'}",
            "Decide and document: is the owner in-kind a contribution or a loan? The choice drives "
            "both the public-support test and whether Schedule L applies.",
            "IRC 170 / Form 990 Schedule L"))

    # 3) Contemporaneous written acknowledgment reminder for contributions >= $250.
    if owner_contrib is not None and owner_contrib >= _ACK_THRESHOLD:
        flags.append(_flag(
            'Contemporaneous written acknowledgment (>= $250 contribution)',
            f"owner in-kind contribution ${owner_contrib:,.2f}",
            "If booked as a contribution, the donor needs a contemporaneous written acknowledgment "
            "(date, amount/description of in-kind, and a no-goods-or-services statement). Confirm "
            "the org issued one for each owner gift >= $250.",
            "IRC 170(f)(8); for non-cash >$5,000 also Form 8283 / qualified appraisal"))

    # 4) Private-benefit / inurement sniff — 50/50 owner-funded, owner-run from Cyprus.
    flags.append(_flag(
        'Private-benefit / inurement sniff',
        "501(c)(3) is 50/50 owner-funded and owner-operated; shared QBO login; "
        "owner personal cards used for program buys (later reclassed in-kind)",
        "Confirm no part of net earnings inures to the owners and that owner transactions are at "
        "arm's length (no below-market benefit flowing back). Document the rebuttable-presumption "
        "process for any owner compensation/reimbursement.",
        "IRC 501(c)(3) inurement prohibition; IRC 4958 intermediate sanctions (excess benefit)"))

    return flags


# --------------------------------------------------------------------------- #
# us_s_corp
# --------------------------------------------------------------------------- #
def _checks_s_corp(ent, bridge):
    flags = []
    tax_ctx = ent.get('tax_context', {}) or {}

    # Surface the attorney flags already curated in config, with regulatory hooks.
    hook_for = [
        ('reasonable officer comp', 'IRC 162 / Rev. Rul. 74-44; Form 1120-S officer compensation'),
        ('feie', 'IRC 911 (FEIE) vs S-corp K-1 pass-through character'),
        ('basis', 'IRC 1366(d)/1367 shareholder stock & debt basis; Form 7203'),
        ('carryforward', 'IRC 170(d) charitable carryforward (flows to shareholder, not the S-corp)'),
        ('schedule l', 'Form 1120-S Schedule L book-vs-return drift'),
        ('fbar', 'FinCEN Form 114 (FBAR) — handled by the foreign-accounts tripwire below'),
    ]

    def _hook(text):
        low = text.lower()
        for needle, hook in hook_for:
            if needle in low:
                return hook
        return 'Form 1120-S / Schedule K-1 — confirm position'

    for f in tax_ctx.get('flags_for_attorney', []) or []:
        flags.append(_flag(
            'S-corp tax position (config flag)',
            f,
            f"Confirm position: {f}",
            _hook(f)))

    # Reasonable-comp tripwire stated explicitly from booked facts when available.
    if tax_ctx.get('payroll'):
        flags.append(_flag(
            'Reasonable officer compensation',
            "payroll=true; verify W-2 officer wages > $0 against shareholder distributions",
            "If officers took distributions with $0 (or token) W-2 wages, is there IRC 162 "
            "reclassification risk (distributions recharacterized as wages + payroll tax)?",
            "IRC 162; Rev. Rul. 74-44; Form 1120-S, Form 941/940"))

    if bridge is not None and bridge.get('corrected_net') is not None:
        flags.append(_flag(
            'Corrected net flows to K-1 (basis & character)',
            f"corrected net ${bridge.get('corrected_net'):,.2f}",
            "Confirm the corrected net flows correctly to each shareholder's K-1 and that stock/debt "
            "basis supports any loss or distribution claimed.",
            "IRC 1366/1367; Form 7203"))

    return flags


# --------------------------------------------------------------------------- #
# BOTH regimes: FBAR / FinCEN-114 tripwire from Bank-of-Cyprus CSVs
# --------------------------------------------------------------------------- #
def _discover_boc(period):
    if not period or not os.path.isdir(period):
        return []
    found = []
    for pat in ('*BOC*', '*TransactionHistory*', '*Transaction History*'):
        found += glob.glob(os.path.join(period, '**', pat), recursive=True)
    # CSVs only, de-duped.
    return sorted({p for p in found if p.lower().endswith('.csv') and os.path.isfile(p)})


def _owner_for_path(path, ent):
    """Best-effort: tag a BOC file to an owner by filename token."""
    base = os.path.basename(path).lower()
    owners = [f.get('name') for f in ent.get('funders', []) if f.get('role') == 'owner_in_kind']
    for full in owners:
        if not full:
            continue
        first = full.split()[0].lower()  # 'Karen', 'Kimberly'
        if first in base:
            return full
    return None


def _peak_running_by_year_eur(df):
    """Reconstruct a CUMULATIVE running balance (carried across years) and return the peak
    magnitude reached WITHIN each calendar year: {year: peak_eur}. This avoids attributing a
    later year's peak to an earlier (sub-threshold) year — the FBAR test is per year."""
    if df is None or df.empty:
        return {}
    import math

    def _f(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if math.isnan(v) else v

    d = df.dropna(subset=['_date']).sort_values('_date')
    running = 0.0
    peaks = {}
    for _, r in d.iterrows():
        running += _f(r.get('_credit', 0)) - _f(r.get('_debit', 0))
        yr = int(r['_date'].year)
        peaks[yr] = max(peaks.get(yr, 0.0), abs(running))
    return {yr: round(p, 2) for yr, p in peaks.items()}


def _checks_fbar(ent, period, year):
    flags = []
    cp = ent.get('compliance_profile', {}) or {}
    if not cp.get('foreign_accounts'):
        return flags

    boc_files = _discover_boc(period)
    if not boc_files:
        flags.append(_flag(
            'FBAR/FinCEN-114 tripwire — inputs not provided',
            "foreign_accounts=true but no BOC CSV found in period "
            "(expected *BOC* or *TransactionHistory*)",
            "Foreign (Cyprus) accounts are configured. Export each Bank-of-Cyprus "
            "TransactionHistory CSV for every year so the $10,000 aggregate FBAR test can run, "
            "then confirm FBAR filing with Anasheh.",
            "31 CFR 1010.350 / FinCEN Form 114 (FBAR), aggregate > $10,000 at any point"))
        return flags

    # Per file: peak EUR balance -> screening USD; aggregate per owner+year.
    by_owner_year = {}   # (owner, yr) -> {'peak_usd': float, 'files': [...]}
    for p in boc_files:
        try:
            df = C.load_boc(p)
        except Exception as e:
            flags.append(_flag(
                'FBAR/FinCEN-114 — BOC file unreadable',
                f"{os.path.basename(p)}: {e}",
                "This Bank-of-Cyprus export could not be parsed; re-export it so the FBAR "
                "aggregate test can include it.",
                "FinCEN Form 114 (FBAR)"))
            continue
        owner = _owner_for_path(p, ent) or 'unidentified owner'
        # Peak balance reached WITHIN each year (not one peak smeared across all years).
        peaks_by_year = _peak_running_by_year_eur(df)
        for yr, peak_eur in peaks_by_year.items():
            peak_usd = round(peak_eur * _EUR_USD_SCREEN, 2)
            key = (owner, yr)
            slot = by_owner_year.setdefault(key, {'peak_usd': 0.0, 'files': []})
            # Aggregate peak across that owner's accounts (FBAR aggregates all accounts).
            slot['peak_usd'] = round(slot['peak_usd'] + peak_usd, 2)
            slot['files'].append(os.path.basename(p))

    for (owner, yr), slot in sorted(by_owner_year.items()):
        if slot['peak_usd'] >= _FBAR_THRESHOLD_USD:
            flags.append(_flag(
                f'FBAR likely required for {owner} — {yr}',
                f"aggregate peak ~${slot['peak_usd']:,.2f} USD-equiv "
                f"(screen rate {_EUR_USD_SCREEN}); files: {', '.join(slot['files'])}",
                f"FBAR likely required for {owner} — confirm filing for {yr} with Anasheh.",
                "31 CFR 1010.350 / FinCEN Form 114 (FBAR), aggregate > $10,000 at any point"))
        else:
            flags.append(_flag(
                f'FBAR screen below threshold for {owner} — {yr}',
                f"aggregate peak ~${slot['peak_usd']:,.2f} USD-equiv (screen rate {_EUR_USD_SCREEN})",
                f"Screening suggests the $10,000 aggregate was NOT crossed for {owner} in {yr}, but "
                "confirm with the Treasury year-end rate and ALL foreign accounts before concluding "
                "no FBAR is due.",
                "31 CFR 1010.350 / FinCEN Form 114 (FBAR)"))
    return flags


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def run(ent, defaults=None, period=None, year=None, pnl=None, bridge=None, **kw):
    """Return regime-aware compliance flags. NEVER raises; degrades gracefully."""
    try:
        ent = ent or {}
        cp = (ent.get('compliance_profile') or {})
        regime = cp.get('regime', 'unknown')
        flags = []

        if regime == 'us_501c3':
            flags += _checks_501c3(ent, bridge)
        elif regime == 'us_s_corp':
            flags += _checks_s_corp(ent, bridge)
        else:
            flags.append(_flag(
                'Unknown compliance regime',
                f"compliance_profile.regime={regime!r}",
                "No regime-specific checks ran. Set compliance_profile.regime to us_501c3 or "
                "us_s_corp in entities.yaml.",
                "n/a"))

        # FBAR applies to BOTH regimes when foreign_accounts is true.
        flags += _checks_fbar(ent, period, year)

        return {
            'regime': regime,
            'foreign_accounts': bool(cp.get('foreign_accounts')),
            'signoff_owner': cp.get('signoff_owner', 'Anasheh (tax attorney)'),
            'disclaimer': "FLAGS ONLY — for Anasheh sign-off, not tax advice. All items status OPEN.",
            'flag_count': len(flags),
            'open_count': sum(1 for f in flags if f.get('status') == 'OPEN'),
            'flags': flags,
        }
    except Exception as e:  # run() must never raise
        return {'error': f"compliance_checks.run failed: {e}",
                'flags': [], 'disclaimer': "FLAGS ONLY — for Anasheh sign-off, not tax advice."}


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser(description="Regime-aware compliance flags (READ-ONLY, flag-only).")
    ap.add_argument('--config', default=os.path.join(HERE, '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity', required=True)
    ap.add_argument('--year', type=int, default=None)
    ap.add_argument('--period', default=None)
    a = ap.parse_args()
    ent, defaults = C.load_config(a.config, a.entity)
    print(json.dumps(run(ent=ent, defaults=defaults, period=a.period, year=a.year), indent=2))
