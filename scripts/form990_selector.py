#!/usr/bin/env python3
"""form990_selector.py — FLAG-ONLY Form 990 family recommendation (501(c)(3) only).

Uses CORRECTED gross receipts (bridge['corrected_income']) and CORRECTED total assets
(from the Balance Sheet, adjusted for the known phantom) to point at the right Form 990
variant. This RECOMMENDS; it never decides. Final form is confirmed with Anasheh.

Thresholds (normal year):
  gross receipts > $50,000              -> NOT eligible for 990-N (e-Postcard)
  gross receipts < $200,000 AND assets < $500,000 -> points to 990-EZ
  otherwise                              -> full 990

Caveats surfaced every run:
  * the $50k/$200k tests are on a PRIOR-YEAR-AVERAGED basis (normally), not a single year
  * the revocation tripwire from config (wrong form historically filed = treated as not
    filing -> automatic revocation after 3 consecutive years)
"""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _common as C

_990N_CEILING = 50000.0       # gross receipts "normally <= $50k" -> 990-N eligible
_EZ_GROSS_CEILING = 200000.0  # gross receipts < $200k AND ...
_EZ_ASSET_CEILING = 500000.0  # ... total assets < $500k -> 990-EZ


def _corrected_total_assets(bs_path, bridge):
    """Best-effort corrected total assets.

    NOTE: load_balance_sheet keys to the LAST number on a line. On a two-column
    (current vs prior-year) QBO export that is the PRIOR-YEAR column, so 'total_assets'
    may be a PY figure — we flag that rather than trust it blindly. The phantom is a
    LIABILITY overstatement (it does not inflate assets), so corrected total assets
    equals reported total assets; we still subtract any explicit asset-side phantom if
    the bridge supplies one.
    """
    if not bs_path or not os.path.exists(bs_path):
        return None, "balance sheet not provided — export the QBO Balance Sheet CSV", None
    try:
        bs = C.load_balance_sheet(bs_path)
    except Exception as e:
        return None, f"balance sheet unreadable: {e}", None
    reported = bs.get('total_assets')
    if reported is None:
        reported = bs.get('Total for Assets')
    note = None
    if reported is not None:
        note = ("reported total assets is parsed from the LAST column of the BS export; on a "
                "current-vs-PY comparison that is the PRIOR-YEAR column — confirm against the "
                "current-year column before relying on it")
    # Asset-side phantom (only if the bridge explicitly carries one; the card phantom is a liability).
    asset_phantom = 0.0
    if bridge:
        asset_phantom = float(bridge.get('asset_phantom') or 0.0)
    corrected = round(reported - asset_phantom, 2) if reported is not None else None
    return corrected, note, bs


def run(ent, defaults=None, year=None, bridge=None, bs_path=None, **kw):
    """Return a flag-only Form 990 family recommendation. NEVER raises."""
    try:
        ent = ent or {}
        cp = ent.get('compliance_profile', {}) or {}
        regime = cp.get('regime', 'unknown')

        if regime != 'us_501c3':
            return {'applicable': False,
                    'note': f"Form 990 family applies to us_501c3 only; this entity is {regime}.",
                    'disclaimer': "FLAG ONLY. Confirm final form with Anasheh."}

        gross = (bridge or {}).get('corrected_income')
        assets, asset_note, _bs = _corrected_total_assets(bs_path, bridge)

        lines = []
        recommendation = None
        if gross is None:
            lines.append("CORRECTED gross receipts not provided — run the bridge "
                         "(bridge['corrected_income']) first; cannot recommend a form.")
        else:
            # 990-N ceiling.
            if gross > _990N_CEILING:
                lines.append(f"Gross receipts ${gross:,.2f} > ${_990N_CEILING:,.0f} (normal) "
                             "-> NOT eligible for 990-N (e-Postcard).")
            else:
                lines.append(f"Gross receipts ${gross:,.2f} <= ${_990N_CEILING:,.0f} -> 990-N may be "
                             "available, BUT see the prior-year-averaging caveat and revocation tripwire.")

            # 990-EZ vs full 990.
            if gross > _990N_CEILING:
                if assets is None:
                    lines.append(f"Gross receipts < ${_EZ_GROSS_CEILING:,.0f}? "
                                 f"({'yes' if gross < _EZ_GROSS_CEILING else 'NO'}); total assets unknown "
                                 f"({asset_note}) -> cannot confirm 990-EZ vs 990. Provide corrected total assets.")
                    recommendation = "990-EZ or 990 (need corrected total assets to choose)"
                elif gross < _EZ_GROSS_CEILING and assets < _EZ_ASSET_CEILING:
                    lines.append(f"Gross receipts ${gross:,.2f} < ${_EZ_GROSS_CEILING:,.0f} AND total assets "
                                 f"${assets:,.2f} < ${_EZ_ASSET_CEILING:,.0f} -> points to 990-EZ.")
                    recommendation = "990-EZ"
                else:
                    lines.append(f"Gross receipts ${gross:,.2f} or total assets ${assets:,.2f} exceeds the "
                                 f"990-EZ ceilings (${_EZ_GROSS_CEILING:,.0f} / ${_EZ_ASSET_CEILING:,.0f}) -> full 990.")
                    recommendation = "990"
            else:
                recommendation = "990-N (subject to caveats below)"

        # Always-on caveats.
        lines.append("CAVEAT (prior-year averaging): the $50k/$200k 'normally' tests are computed on an "
                     "AVERAGE of the current and prior years' gross receipts, not a single year — confirm "
                     "the multi-year average before finalizing.")
        tripwire = cp.get('revocation_tripwire')
        if tripwire:
            lines.append(f"REVOCATION TRIPWIRE: {tripwire}")
        lines.append("Hard rule: filing the WRONG form (e.g. 990-N when 990/990-EZ was required) is treated "
                     "as NOT filing; 3 consecutive years of non-filing -> automatic revocation of exempt status "
                     "(IRC 6033(j)).")
        lines.append("Confirm final form with Anasheh.")

        out = {
            'applicable': True,
            'corrected_gross_receipts': gross,
            'corrected_total_assets': assets,
            'total_assets_note': asset_note,
            'form_candidate': recommendation,   # mechanical pointer from the thresholds — NOT a decision (Anasheh chooses)
            'form_candidate_note': "Threshold-based pointer only; the final 990-series form is Anasheh's call (status OPEN).",
            'thresholds': {'990N_ceiling': _990N_CEILING,
                           '990EZ_gross_ceiling': _EZ_GROSS_CEILING,
                           '990EZ_asset_ceiling': _EZ_ASSET_CEILING},
            'lines': lines,
            'status': 'OPEN',
            'disclaimer': "FLAG ONLY — not tax advice. Confirm final form with Anasheh.",
        }
        # Echo a printable block (close.py captures stdout-free dicts, but the granular CLI prints).
        out['memo'] = "\n".join(lines)
        return out
    except Exception as e:  # run() must never raise
        return {'applicable': None, 'error': f"form990_selector.run failed: {e}",
                'disclaimer': "FLAG ONLY. Confirm final form with Anasheh."}


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser(description="Form 990 family recommendation (501(c)(3), flag-only).")
    ap.add_argument('--config', default=os.path.join(HERE, '..', 'config', 'entities.yaml'))
    ap.add_argument('--entity', required=True)
    ap.add_argument('--year', type=int, default=None)
    ap.add_argument('--bs', default=None, help='path to QBO Balance Sheet CSV')
    ap.add_argument('--corrected-income', type=float, default=None)
    a = ap.parse_args()
    ent, defaults = C.load_config(a.config, a.entity)
    bridge = {'corrected_income': a.corrected_income} if a.corrected_income is not None else None
    res = run(ent=ent, defaults=defaults, year=a.year, bridge=bridge, bs_path=a.bs)
    print(json.dumps(res, indent=2))
    if res.get('memo'):
        print("\n" + res['memo'])
