#!/usr/bin/env python3
"""build_package.py — the Excel close package (entity-parameterized).

Consumes the close_summary.json dict that close.py produces and writes ONE .xlsx
workbook with these tabs, then calls genmemo.generate() to write the Word memo:

  Controls Summary (COVER)  — every controls check (status / counts / $ exposure)
                              + a PREVENTION_SOP checklist.
  Bridge                    — booked net -> remove duplicate expense -> (break-even:
                              + record contributions) -> corrected net. For
                              for_profit: "corrected net = real result; do NOT plug"
                              and an explicit Unexplained-Variance line.
  Per-Account Reconciliation — book P&L charges, real charges, phantom (P&L), book
                              ending balance, real statement balance, BALANCE phantom
                              (carry-in / added-in-year), back-dated rows.
  Duplicate Delete-Set      — tiered pairs with FX delta + register row refs so the
                              bookkeeper deletes line by line. EXACT control total,
                              ESTIMATE per-line, never bulk-delete.
  Evidence Index            — input_manifest (path / sha256 / mtime) for traceability.

INVARIANTS: READ-ONLY (worksheets only — no QBO writes). Tax items live in the memo,
flagged OPEN for sign-off. Plain values, no fragile formulas.

Entry point:  build(out_dir, summary, accounts, bridge, ent, controls) -> path_to_xlsx
build() is defensive and degrades gracefully on missing/optional inputs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

try:
    import genmemo
except Exception:  # memo is optional; the workbook still builds
    genmemo = None

NAVY = "1F3864"
AMBER = "FFF2CC"
GREEN = "E2EFDA"
RED = "FCE4D6"
GREY = "F2F2F2"
WHITE = "FFFFFF"
F = "Arial"
CUR = '$#,##0.00;($#,##0.00)'
INT = '#,##0'
PCT = '0.0"%"'


def ft(sz=10, b=False, c="000000"):
    return Font(name=F, size=sz, bold=b, color=c)


thin = Side(style="thin", color="BFBFBF")
bd = Border(left=thin, right=thin, top=thin, bottom=thin)
LEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="top")
CTR = Alignment(horizontal="center", vertical="top", wrap_text=True)


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _title(ws, text, sub=None):
    ws["A1"] = text
    ws["A1"].font = ft(14, True, NAVY)
    if sub:
        ws["A2"] = sub
        ws["A2"].font = ft(9, False, "808080")


def _hdr(ws, row, cols, start=1):
    for j, name in enumerate(cols, start=start):
        c = ws.cell(row, j, name)
        c.font = ft(9, True, WHITE)
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.border = bd
        c.alignment = CTR
    return row + 1


def _cell(ws, r, j, val, money=False, integer=False, pct=False, bold=False, fill=None, wrap=False):
    c = ws.cell(r, j, val)
    c.font = ft(9, bold)
    c.border = bd
    if money:
        c.number_format = CUR
        c.alignment = RIGHT
    elif integer:
        c.number_format = INT
        c.alignment = RIGHT
    elif pct:
        c.number_format = PCT
        c.alignment = RIGHT
    else:
        c.alignment = LEFT if wrap else Alignment(horizontal="left", vertical="top")
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    return c


def _widths(ws, widths):
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


# --------------------------------------------------------------------------
# Controls normalization (controls modules are written elsewhere; be defensive)
# --------------------------------------------------------------------------
def _norm_controls(controls):
    rows = []
    if not isinstance(controls, dict) or not controls:
        return rows
    for mod, res in controls.items():
        if not isinstance(res, dict):
            rows.append((mod, "not_run", None, str(res)))
            continue
        if 'error' in res:
            rows.append((mod, "not_run", None, f"error: {res['error']}"))
            continue
        status = res.get('status') or res.get('result') or "ran"
        count = res.get('count', res.get('exceptions_count', res.get('n_exceptions')))
        exposure = res.get('dollar_exposure', res.get('exposure', res.get('amount')))
        note = res.get('summary') or res.get('note') or ""
        if count is None:
            f = res.get('findings') or res.get('exceptions') or res.get('items')
            if isinstance(f, list):
                count = len(f)
        rows.append((mod, status, count, exposure, note))
    return rows


# --------------------------------------------------------------------------
# TAB 1 — Controls Summary (COVER)
# --------------------------------------------------------------------------
def _tab_controls(wb, summary, ent, controls):
    ws = wb.active
    ws.title = "Controls Summary"
    legal = ent.get('legal_name') or summary.get('legal_name') or summary.get('entity') or "Entity"
    year = summary.get('year', '')
    _title(ws, f"Forensic Close — Controls Summary", f"{legal} — FY{year}  ·  READ-ONLY worksheet; no QuickBooks records were modified")

    r = 4
    ws.cell(r, 1, "Forensic-controls checks").font = ft(11, True, NAVY)
    r += 1
    r = _hdr(ws, r, ["Check", "Status", "Exceptions", "$ Exposure", "Notes"])
    norm = _norm_controls(controls)
    if not norm:
        _cell(ws, r, 1, "(no controls module installed for this run)", wrap=True)
        _cell(ws, r, 2, "not_run")
        _cell(ws, r, 3, None)
        _cell(ws, r, 4, None)
        _cell(ws, r, 5, "Export the QBO audit log + Balance Sheet and re-run so the OBE / prior-period / "
                        "back-dating / duplicate-scan checks populate.", wrap=True)
        r += 1
    else:
        for row in norm:
            mod, status = row[0], row[1]
            count = row[2] if len(row) > 2 else None
            exposure = row[3] if len(row) > 3 else None
            note = row[4] if len(row) > 4 else ""
            fill = {"clean": GREEN, "exceptions": RED, "not_run": GREY}.get(
                str(status).lower(), AMBER)
            _cell(ws, r, 1, mod, wrap=True)
            _cell(ws, r, 2, status, fill=fill)
            _cell(ws, r, 3, _f(count), integer=count is not None)
            _cell(ws, r, 4, _f(exposure), money=exposure is not None)
            _cell(ws, r, 5, note, wrap=True)
            r += 1

    # PREVENTION SOP checklist
    r += 2
    ws.cell(r, 1, "Prevention SOP — stop creating the phantom").font = ft(11, True, NAVY)
    r += 1
    r = _hdr(ws, r, ["Control", "In place?", "Why it matters"])
    sop = [
        ("Separate logins per user (no shared credentials)",
         "Shared login (Alison logged in AS Kimberly) destroyed the audit trail."),
        ("MFA required on QuickBooks",
         "Multi-factor blocks unauthorized re-use of credentials."),
        ("Bank-feed only — never hand-enter a fed transaction",
         "Manual re-keying at FX amounts is the root cause of the duplicates."),
        ("One QBO account per real card",
         "One card was booked as TWO accounts — the entire phantom origin."),
        ("Reconcile to the statement every month",
         "A clean monthly reconcile makes a five-figure phantom impossible."),
        ("Owner-paid expenses booked with BOTH sides",
         "Book the expense AND the contribution/loan the same month."),
        ("Monthly drift check (QBO balance vs. real statement)",
         "Catch small drift early instead of an annual phantom."),
    ]
    for ctrl, why in sop:
        _cell(ws, r, 1, ctrl, wrap=True)
        _cell(ws, r, 2, "[ ]", fill=AMBER)
        _cell(ws, r, 3, why, wrap=True)
        r += 1

    _widths(ws, {"A": 46, "B": 16, "C": 60, "D": 16, "E": 60})
    return ws


# --------------------------------------------------------------------------
# TAB 2 — Bridge
# --------------------------------------------------------------------------
def _tab_bridge(wb, summary, bridge, ent):
    ws = wb.create_sheet("Bridge")
    legal = ent.get('legal_name') or summary.get('legal_name') or "Entity"
    year = summary.get('year', '')
    funding = summary.get('funding_model', ent.get('funding_model', 'break_even'))
    for_profit = (funding == 'for_profit')
    b = bridge or summary.get('bridge') or {}
    _title(ws, "Bridge to Corrected Net",
           f"{legal} — FY{year}  ·  funding model: {funding}")

    r = 4
    r = _hdr(ws, r, ["Step", "Amount"])
    booked = _f(b.get('booked_net'))
    dupe = _f(b.get('duplicate_expense_removed'))
    contrib = _f(b.get('owner_contrib_to_record'))
    corrected = _f(b.get('corrected_net'))

    rows = [("Booked net income (as recorded in QuickBooks)", booked, False)]
    rows.append(("Remove duplicated card expense (P&L)", dupe, False))
    if for_profit:
        # corrected = booked + dupe expected; unexplained variance surfaces any gap
        expected = (booked or 0) + (dupe or 0)
        variance = (corrected - expected) if corrected is not None else None
        rows.append(("Corrected net = REAL operating result (do NOT plug)", corrected, True))
        rows.append(("Unexplained variance (should be ~$0; investigate if not)", variance, False))
    else:
        rows.append(("Record owner in-kind contributions (break-even funding)", contrib, False))
        rows.append(("CORRECTED NET (target = $0)", corrected, True))

    for lbl, val, hl in rows:
        _cell(ws, r, 1, lbl, bold=hl, wrap=True, fill=AMBER if hl else None)
        _cell(ws, r, 2, val, money=True, bold=hl, fill=AMBER if hl else None)
        r += 1

    r += 1
    if for_profit:
        note = ("FOR-PROFIT: the corrected net is the entity's real result and is NOT forced to a target. "
                "If the Unexplained-Variance line is not ~$0, do not plug it — investigate.")
    else:
        ties = b.get('ties_to_target')
        note = ("BREAK-EVEN: contributions are sized to expenses so net ties to $0. "
                + ("Ties to target: YES." if ties else "WARNING: does NOT tie to target — review."))
    _cell(ws, r, 1, note, wrap=True)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)

    # supporting P&L figures
    r += 2
    ws.cell(r, 1, "Supporting figures").font = ft(10, True, NAVY)
    r += 1
    for lbl, key in [("Booked income", 'booked_income'), ("Booked expenses", 'booked_expenses'),
                     ("Corrected expenses", 'corrected_expenses'), ("Corrected income", 'corrected_income')]:
        if b.get(key) is not None:
            _cell(ws, r, 1, lbl, wrap=True)
            _cell(ws, r, 2, _f(b.get(key)), money=True)
            r += 1

    _widths(ws, {"A": 56, "B": 20})
    return ws


# --------------------------------------------------------------------------
# TAB 3 — Per-Account Reconciliation
# --------------------------------------------------------------------------
def _tab_accounts(wb, summary, accounts):
    ws = wb.create_sheet("Per-Account Reconciliation")
    _title(ws, "Per-Account Reconciliation",
           "Book vs. real card. BALANCE phantom = book ending balance − real statement balance.")
    cols = ["Account", "Book P&L charges", "Real charges", "Phantom (P&L)",
            "Book ending bal", "Real stmt bal", "Balance phantom",
            "  carry-in", "  added-in-year", "Prior-period rows", "Future-dated rows", "Status"]
    r = _hdr(ws, 4, cols)
    accts = accounts or summary.get('accounts', []) or []
    for a in accts:
        if 'phantom_charges_pl' not in a:
            _cell(ws, r, 1, a.get('label', '?'), wrap=True)
            for j in range(2, 12):
                _cell(ws, r, j, None)
            _cell(ws, r, 12, a.get('skipped') or a.get('missing') or "skipped", fill=GREY, wrap=True)
            r += 1
            continue
        _cell(ws, r, 1, a.get('label', '?'), wrap=True)
        _cell(ws, r, 2, _f(a.get('book_charges_pl')), money=True)
        _cell(ws, r, 3, _f(a.get('real_charges')), money=True)
        _cell(ws, r, 4, _f(a.get('phantom_charges_pl')), money=True, fill=AMBER)
        _cell(ws, r, 5, _f(a.get('book_ending_balance')), money=True)
        _cell(ws, r, 6, _f(a.get('real_statement_balance')), money=True)
        _cell(ws, r, 7, _f(a.get('balance_phantom')), money=True, fill=AMBER)
        _cell(ws, r, 8, _f(a.get('balance_phantom_carry_in')), money=True)
        _cell(ws, r, 9, _f(a.get('balance_phantom_added_in_year')), money=True)
        _cell(ws, r, 10, _f(a.get('off_period_priorperiod_rows')), integer=True)
        _cell(ws, r, 11, _f(a.get('off_period_future_rows')), integer=True)
        _cell(ws, r, 12, "reconciled", fill=GREEN)
        r += 1

    r += 1
    _cell(ws, r, 1, "Phantom (P&L) = book P&L charges − real charges. Balance phantom = book ending "
                    "balance − real statement balance, split into prior-year carry-in and amounts added "
                    "this year. Reconcile each card to the REAL statement balance after deleting duplicates.",
          wrap=True)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=12)

    _widths(ws, {"A": 30, "B": 16, "C": 14, "D": 14, "E": 15, "F": 14,
                 "G": 15, "H": 13, "I": 15, "J": 13, "K": 13, "L": 13})
    return ws


# --------------------------------------------------------------------------
# TAB 4 — Duplicate Delete-Set
# --------------------------------------------------------------------------
def _tab_dupes(wb, summary, accounts):
    ws = wb.create_sheet("Duplicate Delete-Set")
    _title(ws, "Duplicate Delete-Set (tiered, line-by-line)",
           "Delete the firmed manual duplicates by __source/__row. Tier 1 first; Tier 2/3 review.")
    note_row = 3
    nc = ws.cell(note_row, 1,
                 "CONTROL TOTAL is EXACT; the per-line split is an ESTIMATE for review. "
                 "NEVER bulk-delete the manual account — part of it is the SOLE legitimate booking of "
                 "real charges. Delete only firmed pairs; route the residual (unmatched) to investigation.")
    nc.font = ft(9, True, "9C0006")
    nc.fill = PatternFill("solid", fgColor=AMBER)
    nc.alignment = LEFT
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=11)

    cols = ["Tier", "Date", "Payee", "Memo", "Category", "Book amount", "Feed amount",
            "FX delta", "Days apart", "Payee sim", "__source", "__row", "memo refs (feed)"]
    accts = accounts or []
    any_pairs = False
    r = 5
    for a in accts:
        pairs = a.get('_pairs_df')
        if pairs is None or getattr(pairs, 'empty', True):
            continue
        any_pairs = True
        lbl = ws.cell(r, 1, f"Account: {a.get('label', '?')}")
        lbl.font = ft(10, True, NAVY)
        r += 1
        r = _hdr(ws, r, cols)
        # iterate rows defensively — accept whatever columns are present
        for _, row in pairs.iterrows():
            g = lambda k: row[k] if k in pairs.columns else None
            tier = g('Tier')
            fill = {1: GREEN, "1": GREEN, 2: AMBER, "2": AMBER, 3: RED, "3": RED}.get(tier, None)
            _cell(ws, r, 1, tier, fill=fill)
            _cell(ws, r, 2, str(g('Date')) if g('Date') is not None else None)
            _cell(ws, r, 3, g('Payee'), wrap=True)
            _cell(ws, r, 4, g('Memo'), wrap=True)
            _cell(ws, r, 5, g('Category'), wrap=True)
            _cell(ws, r, 6, _f(g('book_amount')), money=True)
            _cell(ws, r, 7, _f(g('feed_amount')), money=True)
            _cell(ws, r, 8, _f(g('fx_delta')), money=True)
            _cell(ws, r, 9, _f(g('days_apart')), integer=True)
            _cell(ws, r, 10, _f(g('payee_sim')))
            _cell(ws, r, 11, g('__source'))
            _cell(ws, r, 12, _f(g('__row')), integer=True)
            _cell(ws, r, 13, g('memo_refs_feed'), wrap=True)
            r += 1
        # per-account control vs estimate
        dp = a.get('duplicate_pairing') or {}
        r += 1
        _cell(ws, r, 1, "Control total (EXACT)", bold=True)
        _cell(ws, r, 6, _f(dp.get('control_total_phantom_pl', a.get('phantom_charges_pl'))), money=True, bold=True, fill=AMBER)
        r += 1
        _cell(ws, r, 1, "Matched-pair total (ESTIMATE)", bold=True)
        _cell(ws, r, 6, _f(dp.get('matched_duplicate_total')), money=True, bold=True)
        r += 1
        _cell(ws, r, 1, "Residual unmatched (INVESTIGATE — do not bulk-delete)", bold=True)
        _cell(ws, r, 6, _f(dp.get('residual_unmatched_phantom')), money=True, bold=True, fill=RED)
        r += 2

    if not any_pairs:
        _cell(ws, r, 1, "No duplicate pairs DataFrame was attached to any account (the live close.py run "
                        "attaches '_pairs_df'). Re-run close.py with both register exports so the tiered "
                        "delete-set populates. The summary's duplicate_pairing block still reports the "
                        "control total and coverage.", wrap=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=11)
        # still surface the summary-level control numbers
        r += 2
        for a in accts:
            dp = a.get('duplicate_pairing') or {}
            if not dp:
                continue
            _cell(ws, r, 1, f"{a.get('label','?')} — control total (EXACT)", bold=True, wrap=True)
            _cell(ws, r, 6, _f(dp.get('control_total_phantom_pl')), money=True, bold=True, fill=AMBER)
            r += 1
            _cell(ws, r, 1, f"{a.get('label','?')} — matched estimate / coverage", wrap=True)
            _cell(ws, r, 6, _f(dp.get('matched_duplicate_total')), money=True)
            _cell(ws, r, 7, _f(dp.get('coverage_pct')), pct=True)
            r += 2

    _widths(ws, {"A": 24, "B": 12, "C": 22, "D": 30, "E": 18, "F": 13, "G": 13,
                 "H": 11, "I": 11, "J": 10, "K": 16, "L": 9, "M": 30})
    return ws


# --------------------------------------------------------------------------
# TAB 5 — Evidence Index
# --------------------------------------------------------------------------
def _tab_evidence(wb, summary):
    ws = wb.create_sheet("Evidence Index")
    _title(ws, "Evidence Index — input manifest",
           "Every number traces to these files. sha256 + mtime make the close reproducible.")
    r = _hdr(ws, 4, ["File path", "sha256", "Modified", "Bytes"])
    manifest = summary.get('input_manifest', []) or []
    if not manifest:
        _cell(ws, r, 1, "(no input manifest in summary — close.py records file_manifest of the period exports)", wrap=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        r += 1
    for m in manifest:
        _cell(ws, r, 1, m.get('path'), wrap=True)
        _cell(ws, r, 2, m.get('sha256'))
        _cell(ws, r, 3, str(m.get('mtime')))
        _cell(ws, r, 4, _f(m.get('bytes')), integer=True)
        r += 1
    _widths(ws, {"A": 70, "B": 66, "C": 22, "D": 12})
    return ws


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def build(out_dir, summary, accounts=None, bridge=None, ent=None, controls=None, **kw):
    """Build the Excel close package and (best-effort) the Word memo.
    Never raises; returns the xlsx path (or a sentinel on catastrophic failure)."""
    try:
        return _build(out_dir or ".", summary or {}, accounts, bridge, ent or {}, controls or {})
    except Exception as e:
        try:
            os.makedirs(out_dir or ".", exist_ok=True)
            p = os.path.join(out_dir or ".", "close_package_ERROR.txt")
            with open(p, "w") as fh:
                fh.write(f"build_package.build failed: {e}\n")
            return p
        except Exception:
            return f"<build failed: {e}>"


def _build(out_dir, summary, accounts, bridge, ent, controls):
    os.makedirs(out_dir, exist_ok=True)
    accounts = accounts if accounts is not None else summary.get('accounts', [])
    bridge = bridge if bridge is not None else summary.get('bridge')
    controls = controls if controls is not None else summary.get('controls', {})

    wb = Workbook()
    _tab_controls(wb, summary, ent, controls)
    _tab_bridge(wb, summary, bridge, ent)
    _tab_accounts(wb, summary, accounts)
    _tab_dupes(wb, summary, accounts)
    _tab_evidence(wb, summary)

    entity = summary.get('entity', ent.get('legal_name', 'entity'))
    year = summary.get('year', '')
    path = os.path.join(out_dir, f"close_package_{entity}_{year}.xlsx")
    wb.save(path)

    # Build the companion findings memo (never let it break the workbook return).
    if genmemo is not None:
        try:
            genmemo.generate(out_dir=out_dir, summary=summary, bridge=bridge, ent=ent, controls=controls)
        except Exception:
            pass
    return path


if __name__ == '__main__':
    import json
    sj = sys.argv[1] if len(sys.argv) > 1 else None
    out = sys.argv[2] if len(sys.argv) > 2 else "."
    s = json.load(open(sj)) if sj else {}
    print(build(out, s, s.get('accounts'), s.get('bridge'), {}, s.get('controls')))
