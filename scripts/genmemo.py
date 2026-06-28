#!/usr/bin/env python3
"""genmemo.py — the forensic findings memo (Word .docx).

Companion to build_package.py. Produces the narrative Kimberly hands to her
team alongside the Excel close package:

  SECTION A — Bookkeeping fixes (for Leigh Ann, the bookkeeper): consolidate the
              duplicated card into the one real account, delete the firmed
              duplicates (per the Delete-Set tab), reconcile to the real
              statement balance, clear the OBE / prior-period / back-dating
              footprints surfaced by the controls checks.
  SECTION B — Tax items (for Anasheh, the tax attorney): each carries an amount
              (the FACT), a question (the DECISION), a regulatory hook, and
              status OPEN. NEVER a decided tax treatment.

INVARIANTS honored here:
  * READ-ONLY narrative. Nothing here edits QuickBooks; every mutation is a
    recommendation for a human to execute.
  * FLAG-BUT-NEVER-DECIDE on tax. Section B states facts and questions only.
  * Bookkeeping (A) and tax (B) are strictly separated.

Entry point:  generate(out_dir, summary, bridge, ent, controls) -> path_to_docx
The generate() function accepts **kw and NEVER raises.
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

NAVY = RGBColor(0x1F, 0x38, 0x64)
AMBER_TXT = RGBColor(0x9C, 0x60, 0x00)
GREY = RGBColor(0x59, 0x59, 0x59)


def _money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return f"(${abs(v):,.2f})" if v < 0 else f"${v:,.2f}"


def _heading(doc, text, size=14, color=NAVY, space_before=10):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(size)
    r.font.color.rgb = color
    return p


def _body(doc, text, bold=False, italic=False, color=None, size=10.5, bullet=False):
    p = doc.add_paragraph(style="List Bullet" if bullet else None)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text)
    r.bold = bold
    r.italic = italic
    r.font.size = Pt(size)
    if color is not None:
        r.font.color.rgb = color
    return p


def _control_findings(controls):
    """Normalize the controls-module outputs into a flat list of human findings.
    Controls modules are written by another part of the toolkit and may not all
    be installed; degrade gracefully on whatever shape (or absence) we get."""
    out = []
    if not isinstance(controls, dict):
        return out
    for mod, res in controls.items():
        if not isinstance(res, dict):
            out.append((mod, str(res), None, None))
            continue
        if 'error' in res:
            out.append((mod, f"check did not run cleanly: {res['error']}", None, "not_run"))
            continue
        status = res.get('status') or res.get('result')
        count = res.get('count', res.get('exceptions_count', res.get('n_exceptions')))
        exposure = res.get('dollar_exposure', res.get('exposure', res.get('amount')))
        findings = res.get('findings') or res.get('exceptions') or res.get('items')
        if isinstance(findings, list) and findings:
            for f in findings:
                if isinstance(f, dict):
                    desc = f.get('description') or f.get('memo') or f.get('account') or str(f)
                    amt = f.get('amount', f.get('dollar_exposure'))
                    out.append((mod, desc, amt, status))
                else:
                    out.append((mod, str(f), None, status))
        else:
            summ = res.get('summary') or res.get('note')
            if summ or status or exposure is not None:
                bits = []
                if status:
                    bits.append(f"status {status}")
                if count is not None:
                    bits.append(f"{count} exception(s)")
                if exposure is not None:
                    bits.append(f"exposure {_money(exposure)}")
                out.append((mod, summ or "; ".join(bits) or "ran", exposure, status))
    return out


def generate(out_dir=None, summary=None, bridge=None, ent=None, controls=None, **kw):
    """Build the findings memo. Never raises; returns the docx path (or a sentinel
    error path-less string if something truly unexpected happens)."""
    try:
        return _generate(out_dir or ".", summary or {}, bridge or {}, ent or {}, controls or {})
    except Exception as e:  # a deliverable builder must degrade, not crash the close
        try:
            os.makedirs(out_dir or ".", exist_ok=True)
            path = os.path.join(out_dir or ".", "findings_memo_ERROR.txt")
            with open(path, "w") as fh:
                fh.write(f"genmemo.generate failed to render the memo: {e}\n")
            return path
        except Exception:
            return f"<memo generation failed: {e}>"


def _generate(out_dir, summary, bridge, ent, controls):
    os.makedirs(out_dir, exist_ok=True)
    legal = ent.get('legal_name') or summary.get('legal_name') or summary.get('entity') or "Entity"
    year = summary.get('year', ent.get('compliance_profile', {}).get('tax_year', ''))
    funding = summary.get('funding_model', ent.get('funding_model', 'break_even'))
    for_profit = (funding == 'for_profit')
    comp = ent.get('compliance_profile', {}) or {}
    accts = summary.get('accounts', []) or []
    live = [a for a in accts if 'phantom_charges_pl' in a]

    doc = Document()
    doc.styles['Normal'].font.name = 'Calibri'
    doc.styles['Normal'].font.size = Pt(10.5)

    # ---- Title block ----
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.LEFT
    tr = t.add_run(f"Forensic Close — Findings Memo")
    tr.bold = True
    tr.font.size = Pt(18)
    tr.font.color.rgb = NAVY
    sub = doc.add_paragraph()
    sr = sub.add_run(f"{legal} — Fiscal Year {year}")
    sr.font.size = Pt(12)
    sr.font.color.rgb = GREY
    meta = doc.add_paragraph()
    mr = meta.add_run("READ-ONLY worksheet package. No QuickBooks records were modified. "
                      "Every consolidation and deletion below is a recommendation for the bookkeeper to execute.")
    mr.italic = True
    mr.font.size = Pt(9)
    mr.font.color.rgb = GREY

    # ---- Cover paragraph ----
    _heading(doc, "Overview", size=13)
    total_phantom = summary.get('total_phantom_pl', sum(a.get('phantom_charges_pl', 0) or 0 for a in live))
    bal_phantom = sum(a.get('balance_phantom', 0) or 0 for a in live)
    corrected_net = (bridge or {}).get('corrected_net')
    if for_profit:
        cover = (f"This memo reports the forensic close of {legal} for FY{year}. The books carried "
                 f"a duplicate-expense overstatement of {_money(total_phantom)} (one real credit card "
                 f"booked as two QuickBooks accounts, with bank-feed charges re-entered by hand at FX "
                 f"amounts) and a card-liability overstatement of {_money(bal_phantom)}. Removing the "
                 f"duplicated expense yields a corrected net of {_money(corrected_net)} — this is the "
                 f"REAL operating result and is NOT plugged to any target. Section A lists the "
                 f"bookkeeping corrections; Section B flags open tax questions for sign-off.")
    else:
        contrib = (bridge or {}).get('owner_contrib_to_record')
        cover = (f"This memo reports the forensic close of {legal} for FY{year}, a break-even entity "
                 f"(target net = $0). The books carried a duplicate-expense overstatement of "
                 f"{_money(total_phantom)} (one real credit card booked as two QuickBooks accounts, "
                 f"with bank-feed charges re-entered by hand at FX amounts) and a card-liability "
                 f"overstatement of {_money(bal_phantom)}, which together produced a fictitious loss. "
                 f"Removing the duplicated expense and recording {_money(contrib)} of owner in-kind "
                 f"contributions restores the true net of {_money(corrected_net)}. Section A lists the "
                 f"bookkeeping corrections; Section B flags open tax questions for sign-off.")
    _body(doc, cover)

    # ============================================================
    # SECTION A — BOOKKEEPING FIXES (Leigh Ann)
    # ============================================================
    _heading(doc, "SECTION A — Bookkeeping Fixes (for Leigh Ann, bookkeeper)", size=14)
    _body(doc, "Execute these in QuickBooks. Work from the accompanying Excel close package; "
               "tab names are referenced in brackets.", italic=True, color=GREY)

    # A1 consolidate
    _heading(doc, "A1. Consolidate the duplicated card into ONE real account", size=12)
    for a in live:
        regs = ", ".join(a.get('registers', []) or [])
        _body(doc,
              f"{a.get('label', 'Account')}: this is ONE real card recorded as TWO QuickBooks accounts "
              f"({regs}). Merge the manual/duplicate account into the single account that mirrors the "
              f"bank feed. Do NOT keep both. [Per-Account Reconciliation]", bullet=True)
    if not live:
        _body(doc, "No live card reconciliation was present in the summary — confirm the QBO register "
                   "exports were supplied for the period.", bullet=True)

    # A2 delete firmed duplicates
    _heading(doc, "A2. Delete the firmed duplicate entries — line by line", size=12)
    _body(doc, "Use the [Duplicate Delete-Set] tab. Each row carries the register source and row number "
               "(__source / __row) so you can delete the exact manual entry, plus the FX delta versus the "
               "real feed line it duplicates.", bullet=True)
    _body(doc, "The control total of duplicated expense is EXACT; the per-line tiered split is an ESTIMATE "
               "for review. Tier 1 = strong matches, safe to delete first; Tier 2/3 = review before deleting.",
          bullet=True)
    _body(doc, "DO NOT bulk-delete the manual account. Part of it is the SOLE legitimate booking of real "
               "charges. Delete only the firmed pairs; route the residual (unmatched) entries to investigation.",
          bullet=True, bold=True)
    for a in live:
        dp = a.get('duplicate_pairing') or {}
        if dp:
            _body(doc,
                  f"{a.get('label','Account')}: {dp.get('matched_as_duplicate', dp.get('tier1', 0))} firmed "
                  f"duplicate(s) totaling {_money(dp.get('matched_duplicate_total'))} of the "
                  f"{_money(dp.get('control_total_phantom_pl', a.get('phantom_charges_pl')))} control total "
                  f"({dp.get('coverage_pct', 'n/a')}% covered); residual to investigate "
                  f"{_money(dp.get('residual_unmatched_phantom'))}.", bullet=True)

    # A3 reconcile to real statement
    _heading(doc, "A3. Reconcile each card to the REAL statement balance", size=12)
    for a in live:
        _body(doc,
              f"{a.get('label','Account')}: book ending balance {_money(a.get('book_ending_balance'))} vs "
              f"real statement balance {_money(a.get('real_statement_balance'))} — a balance phantom of "
              f"{_money(a.get('balance_phantom'))} "
              f"(carry-in {_money(a.get('balance_phantom_carry_in'))} + added-in-year "
              f"{_money(a.get('balance_phantom_added_in_year'))}). After deleting the duplicates, run QBO "
              f"Reconcile against the real statement balance and confirm it clears. [Per-Account Reconciliation]",
              bullet=True)
        pp = a.get('off_period_priorperiod_rows')
        if pp or a.get('off_period_future_rows'):
            _body(doc,
                  f"{a.get('label','Account')}: the rolling register export contains {pp or 0} prior-period and "
                  f"{a.get('off_period_future_rows', 0)} future-dated rows. This is normal for a mid-cycle export and "
                  f"is NOT tampering by itself; confirm entry dates in the QBO Audit Log only if a row is dated into a "
                  f"closed period.", bullet=True)
        if a.get('sign_flag'):
            _body(doc, f"{a.get('label','Account')}: {a['sign_flag']}.", bullet=True)

    # A4 forced-reconciliation / OBE / prior-period cleanup
    _heading(doc, "A4. Clear forced-reconciliation footprints (OBE / prior-period adjustments / deleted-account plugs)", size=12)
    cf = _control_findings(controls)
    if cf:
        for mod, desc, amt, status in cf:
            tag = f" [{status}]" if status else ""
            amt_s = f" — {_money(amt)}" if amt is not None else ""
            _body(doc, f"({mod}{tag}) {desc}{amt_s}", bullet=True)
    else:
        _body(doc, "Forensic-controls checks were not present in this run (controls module not installed "
                   "or produced no findings). Export the QBO audit log and Balance Sheet and re-run so the "
                   "Opening Balance Equity, prior-period-adjustment, and back-dating checks can populate. "
                   "Known footprints to clear from the Balance Sheet: 'Opening balance equity', "
                   "'Prior period adjustments', and any 'Personal expenses (deleted)' plug.", bullet=True)
        _body(doc, "Do not carry an Opening Balance Equity balance forward; trace it to the originating "
                   "entries and reclassify. Prior-period adjustments and deleted-account plugs are "
                   "forced-reconciliation residue — reverse them against the correct accounts, not a catch-all.",
              bullet=True)

    # ============================================================
    # SECTION B — TAX ITEMS (Anasheh)
    # ============================================================
    _heading(doc, "SECTION B — Tax Items (for Anasheh, tax attorney — sign-off required)", size=14)
    _body(doc, "Each item below states the AMOUNT (fact) and the QUESTION (decision) with a regulatory "
               "hook. These are OPEN and are not tax advice. The bookkeeping in Section A does not depend "
               "on how these resolve.", italic=True, color=GREY)

    items = _tax_items(for_profit, summary, bridge, ent, comp)
    for i, it in enumerate(items, 1):
        _heading(doc, f"B{i}. {it['title']}", size=12, color=NAVY, space_before=8)
        _tax_field(doc, "Amount (fact)", it['amount'])
        _tax_field(doc, "Question (decision)", it['question'])
        _tax_field(doc, "Regulatory hook", it['hook'])
        _tax_field(doc, "Status", "OPEN — for "
                   + (comp.get('signoff_owner') or "Anasheh (tax attorney)") + " sign-off", amber=True)

    # ---- Footer ----
    doc.add_paragraph()
    foot = doc.add_paragraph()
    fr = foot.add_run("Prepared with AI assistance (Claude). Bookkeeping fixes for Leigh Ann; tax "
                      "positions for Anasheh sign-off — not tax advice.")
    fr.italic = True
    fr.font.size = Pt(8.5)
    fr.font.color.rgb = GREY

    path = os.path.join(out_dir, f"findings_memo_{summary.get('entity', 'entity')}_{year}.docx")
    doc.save(path)
    return path


def _tax_field(doc, label, value, amber=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.left_indent = Inches(0.25)
    lr = p.add_run(f"{label}: ")
    lr.bold = True
    lr.font.size = Pt(10)
    vr = p.add_run(str(value))
    vr.font.size = Pt(10)
    if amber:
        vr.bold = True
        vr.font.color.rgb = AMBER_TXT


def _tax_items(for_profit, summary, bridge, ent, comp):
    """Flag-but-never-decide tax items. Amount = fact; question = decision."""
    items = []
    if not for_profit:
        contrib = (bridge or {}).get('owner_contrib_to_record')
        items.append({
            'title': "Owner in-kind funding: contribution vs. loan",
            'amount': f"{_money(contrib)} of owner-paid program costs to be recorded.",
            'question': "Should the owner in-kind funding be characterized as a charitable contribution "
                        "to the 501(c)(3) or as a loan/payable to the owner? The bridge currently records "
                        "it as contributed income to reach break-even; the legal characterization is undecided.",
            'hook': "IRC §170 (charitable contribution) vs. bona-fide debt; donor substantiation; "
                    "related-party considerations.",
        })
        forms = ", ".join(comp.get('return_forms', [])) or "990 / 990-EZ"
        items.append({
            'title': "Form 990 selection and revocation tripwire",
            'amount': f"Corrected gross receipts ~{_money((bridge or {}).get('corrected_income'))}; "
                      f"required form(s): {forms}.",
            'question': "Which 990-series form is required, and was the correct form filed for prior years? "
                        "Historically 990-N was filed; if receipts exceed the ceiling, 990-N is the wrong form "
                        "and is treated as non-filing. Confirm what was filed for this year and the three-year "
                        "revocation exposure.",
            'hook': "IRC §6033; Rev. Proc. filing thresholds; automatic revocation after 3 consecutive "
                    "non-filings (IRC §6033(j)).",
        })
        if comp.get('foreign_accounts'):
            items.append({
                'title': "FBAR / FinCEN 114 — foreign (Cyprus) accounts",
                'amount': "Bank of Cyprus owner accounts (EUR) used to fund program costs.",
                'question': "Do the Cypriot accounts trigger an FBAR / FinCEN 114 filing obligation for the "
                            "entity and/or the owners, and were prior-year FBARs filed for each person?",
                'hook': "31 U.S.C. §5314; FinCEN Form 114 ($10,000 aggregate threshold).",
            })
    else:
        tc = (ent.get('tax_context') or {}).get('flags_for_attorney') or []
        # Map the configured for-profit flags into amount/question/hook items.
        defaults = [
            ("Reasonable officer compensation",
             "Officer took ordinary income + distributions but $0 W-2 wages.",
             "Should officer compensation be reclassified to wages? $0 wages on an S-corp with distributions "
             "risks an IRC §162 / employment-tax reclassification.",
             "IRC §162; §3121; Rev. Rul. 74-44 (S-corp reasonable comp)."),
            ("FEIE applied to S-corp K-1 pass-through",
             "Foreign Earned Income Exclusion applied to K-1 pass-through income.",
             "Is FEIE on S-corp pass-through (non-wage) income a supportable position? Flagged as aggressive.",
             "IRC §911 (earned income definition); pass-through is generally not 'earned income'."),
            ("FBAR / FinCEN 114 — Cyprus accounts",
             "Cypriot accounts; Karen filed historically, Kimberly did not.",
             "Confirm each owner's FBAR filing status for each year and remediate gaps.",
             "31 U.S.C. §5314; FinCEN Form 114."),
            ("Shareholder basis asymmetry",
             "~$67K beginning stock-basis asymmetry between shareholders.",
             "Confirm beginning stock/debt basis per shareholder and the source of the asymmetry.",
             "IRC §1366/§1367 (S-corp basis); Form 7203."),
            ("Charitable carryforwards",
             "~$128K charitable carryforwards with no current benefit.",
             "Confirm validity and expiration of the carryforwards; whether any are usable.",
             "IRC §170(d) (carryforward limits)."),
            ("Schedule L drift (QBO vs. return)",
             "~$6,600 Schedule L drift between QBO and the filed return.",
             "Reconcile the balance-sheet drift between QBO and the as-filed Schedule L.",
             "Form 1120-S Schedule L / M-1 / M-2 tie-out."),
        ]
        for title, amt, q, hook in defaults:
            items.append({'title': title, 'amount': amt, 'question': q, 'hook': hook})
        # If config carried extra free-text flags not covered above, append them verbatim as OPEN questions.
        covered = "reasonable comp feie fbar basis charitable schedule l".split()
        for raw in tc:
            low = raw.lower()
            if not any(c in low for c in covered):
                items.append({'title': "Additional flagged item",
                              'amount': "see description", 'question': raw,
                              'hook': "for attorney review"})
    return items


if __name__ == '__main__':
    import json, sys
    sj = sys.argv[1] if len(sys.argv) > 1 else None
    out = sys.argv[2] if len(sys.argv) > 2 else "."
    s = json.load(open(sj)) if sj else {}
    print(generate(out, s, s.get('bridge'), {}, s.get('controls')))
