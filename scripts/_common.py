"""Shared loaders + reconciliation math for the nonprofit/business close.

Grounded in the real export formats: QBO register .xls, the bank/card transaction
CSVs (Capital One, Chase, Wells Fargo, Amex, PayPal), Bank of Cyprus EUR CSV,
QBO P&L / Balance Sheet CSV, and Apple .numbers working files.

INVARIANT: every function here READS data. Nothing in this module (or this skill)
edits QuickBooks. Consolidations/deletions are executed by a human (the bookkeeper).
"""
import re, glob, os, hashlib, datetime as _dt
import pandas as pd

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


# --------------------------------------------------------------------------- #
# Numbers & config
# --------------------------------------------------------------------------- #
def to_num(x, eu=False, strict=False):
    """Parse a money string to float. eu=True treats '.' as thousands and ',' as decimal.
    strict=True raises on an unparseable non-empty value instead of returning 0.0."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s == '' or s.lower() == 'nan':
        return 0.0
    neg = (s.startswith('(') and s.endswith(')')) or s.startswith('-')
    s = s.replace('(', '').replace(')', '').replace('$', '').replace('€', '').replace('\xa0', ' ').strip()
    s = s.lstrip('-')
    s = s.replace('.', '').replace(',', '.') if eu else s.replace(',', '')
    try:
        v = float(s)
    except ValueError:
        if strict:
            raise ValueError(f"unparseable amount: {x!r}")
        return 0.0
    return -v if neg else v


def load_config(path, entity=None):
    """Load entities.yaml. With `entity`, return (entity_block, defaults)."""
    if yaml is None:
        raise RuntimeError("pyyaml not installed — run ./setup.sh")
    with open(path, encoding='utf-8') as fh:
        cfg = yaml.safe_load(fh)
    if entity is None:
        return cfg
    ents = cfg.get('entities', {})
    if entity not in ents:
        raise KeyError(f"entity '{entity}' not in {path}. Known: {list(ents)}")
    return ents[entity], cfg.get('defaults', {})


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def file_manifest(paths):
    """Pin inputs for reproducibility: [{path, sha256, mtime, bytes}]."""
    out = []
    for p in paths:
        if p and os.path.exists(p):
            st = os.stat(p)
            out.append({'path': os.path.abspath(p), 'sha256': sha256(p),
                        'mtime': _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'),
                        'bytes': st.st_size})
    return out


# --------------------------------------------------------------------------- #
# QBO register (.xls) — the "book" side
# --------------------------------------------------------------------------- #
REG_COLS = ['Date', 'RefNo', 'Payee', 'Memo', 'Charge', 'Payment', 'RecStatus', 'Balance', 'Type', 'Account', 'AddedIn']
# QBO accounts that are NOT P&L expense (exclude from expense reconciliation).
PL_EXCLUDE = {'Accounts Payable (A/P)', 'Opening balance equity', 'Shareholder distributions',
              'Bank - Chase Cat Checking 1185', 'Saint Gertrude'}
# Accounts whose presence/balance is itself a forced-reconciliation red flag.
CONTROL_FLAG_ACCOUNTS = {'Opening balance equity', 'Opening Balance Equity',
                         'Reconciliation Discrepancies', 'Reconciliation Discrepancy',
                         'Ask My Accountant', 'Uncategorized Asset'}


def load_qbo_register(path):
    """QBO credit-card register export (.xls). Row0 = account name + balances; row1 = headers.
    Returns (df, header_ending_balance). df keeps the running 'Balance' column."""
    raw = pd.read_excel(path, engine='xlrd', header=None)
    header = str(raw.iloc[0, 0])
    df = pd.read_excel(path, engine='xlrd', header=None, skiprows=2)
    df = df.iloc[:, :len(REG_COLS)]
    df.columns = REG_COLS[:df.shape[1]]
    df['Charge'] = pd.to_numeric(df['Charge'], errors='coerce')
    df['Payment'] = pd.to_numeric(df['Payment'], errors='coerce')
    df['Balance'] = pd.to_numeric(df['Balance'], errors='coerce')
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df['__source'] = os.path.basename(path)
    df['__row'] = df.index + 3  # 1-based row in the original sheet (row0 title, row1 header)
    df = df[df['Date'].notna()].copy()
    m = re.search(r'Ending Balance:\s*\$?\(?([\d,.\-]+)\)?', header)
    return df, (to_num(m.group(1)) if m else None)


def register_asof_balance(df, year):
    """As-of-(Dec 31, year) running balance from the register's own Balance column.
    The header 'Ending Balance' can include later-year rows; this returns the true year-end book balance."""
    in_or_before = df[df['Date'] <= pd.Timestamp(year, 12, 31)]
    if in_or_before.empty:
        return 0.0
    last = in_or_before.sort_values('Date').iloc[-1]
    return float(last['Balance']) if pd.notna(last['Balance']) else 0.0


def split_period(df, year):
    """Split a register into in-year and off-period frames; never silently drop the off-period rows.

    Off-period rows are INFORMATIONAL, not by themselves a tampering tell: a rolling QBO export
    naturally contains prior- and next-period rows (e.g. a register pulled in mid-2026 carries 2026
    activity). True back-dating is a transaction whose ENTRY/modified date (from the Audit Log)
    postdates a CLOSED period — that is detected in audit_log.py, not here."""
    iny = df[df['Date'].dt.year == year].copy()
    off = df[df['Date'].dt.year != year].copy()
    return iny, off


# --------------------------------------------------------------------------- #
# Real bank / card CSVs — the "truth" side (schema-dispatched)
# --------------------------------------------------------------------------- #
def _cols_lower(df):
    return {c.lower().strip(): c for c in df.columns}


def load_real_card(path, schema='capital_one'):
    """Load a real bank/card transaction file into a frame with _date, _debit, _credit (USD).
    _debit = money that increased the card balance (a charge/spend); _credit = a payment/refund."""
    schema = (schema or 'capital_one').lower()
    if schema == 'numbers':
        df = load_numbers(path)
    elif path.lower().endswith(('.xlsx', '.xls')):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    cols = _cols_lower(df)

    def datecol():
        for k in ('transaction date', 'date', 'posting date', 'trans date'):
            if k in cols:
                return cols[k]
        return list(df.columns)[0]

    df['_date'] = pd.to_datetime(df[datecol()], errors='coerce')

    if schema == 'capital_one':
        df['_debit'] = df[cols['debit']].apply(to_num) if 'debit' in cols else 0.0
        df['_credit'] = df[cols['credit']].apply(to_num) if 'credit' in cols else 0.0
    elif schema in ('chase', 'wells_fargo', 'amx', 'paypal', 'signed'):
        amtcol = next((cols[k] for k in ('amount', 'gross', 'net') if k in cols), None)
        if amtcol is None and schema == 'wells_fargo':
            # WF is headerless: Date, Amount(signed), *, *, Description
            df = pd.read_csv(path, header=None)
            df['_date'] = pd.to_datetime(df[0], errors='coerce')
            amt = df[1].apply(to_num)
            df['_debit'] = amt.apply(lambda v: -v if v < 0 else 0.0)
            df['_credit'] = amt.apply(lambda v: v if v > 0 else 0.0)
            df['_desc'] = df[4].astype(str) if df.shape[1] > 4 else ''
            df['__source'] = os.path.basename(path)
            return df
        amt = df[amtcol].apply(to_num)
        # For a credit card, a charge is typically a negative amount on bank exports.
        df['_debit'] = amt.apply(lambda v: -v if v < 0 else 0.0)
        df['_credit'] = amt.apply(lambda v: v if v > 0 else 0.0)
    else:
        df['_debit'] = df[cols['debit']].apply(to_num) if 'debit' in cols else 0.0
        df['_credit'] = df[cols['credit']].apply(to_num) if 'credit' in cols else 0.0

    desc = next((cols[k] for k in ('description', 'payee', 'name', 'merchant') if k in cols), None)
    df['_desc'] = df[desc].astype(str) if desc else ''
    df['__source'] = os.path.basename(path)
    return df


def load_boc(path):
    """Bank of Cyprus transaction history CSV (EUR, EU number format, preamble rows)."""
    raw = open(path, encoding='utf-8', errors='replace').read().splitlines()
    hi = next((i for i, l in enumerate(raw) if l.startswith('Date,')), 0)
    df = pd.read_csv(path, skiprows=hi)
    df['_date'] = pd.to_datetime(df['Date'], errors='coerce', dayfirst=True)
    df['_debit'] = df['Debit'].apply(lambda x: to_num(x, eu=True)) if 'Debit' in df.columns else 0.0
    df['_credit'] = df['Credit'].apply(lambda x: to_num(x, eu=True)) if 'Credit' in df.columns else 0.0
    df['__source'] = os.path.basename(path)
    return df


def load_numbers(path):
    """Read an Apple .numbers file's first table into a DataFrame."""
    from numbers_parser import Document
    doc = Document(path)
    table = doc.sheets[0].tables[0]
    rows = table.rows(values_only=True)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows[1:], columns=[str(c) for c in rows[0]])


# --------------------------------------------------------------------------- #
# QBO P&L / Balance Sheet CSVs
# --------------------------------------------------------------------------- #
def load_pnl(path, strict=True):
    """Parse a QBO P&L CSV. Captures each labeled subtotal explicitly and cross-foots.
    Returns a dict with: income, cogs, gross_profit, expenses, operating_net,
    other_income, other_expenses, net. Missing optional sections default to 0.0."""
    text = open(path, encoding='utf-8', errors='replace').read().splitlines()
    out = {}

    def last_num(line):
        nums = re.findall(r'\(?-?\$?[\d,]+\.\d{2}\)?', line)
        return to_num(nums[-1]) if nums else None

    label_map = [
        ('total for income', 'income'), ('total income', 'income'),
        ('total for cost of goods sold', 'cogs'), ('total cogs', 'cogs'), ('total cost of goods sold', 'cogs'),
        ('gross profit', 'gross_profit'),
        ('total for expenses', 'expenses'), ('total expenses', 'expenses'),
        ('net operating income', 'operating_net'),
        ('total for other income', 'other_income'), ('total other income', 'other_income'),
        ('total for other expenses', 'other_expenses'), ('total other expenses', 'other_expenses'),
        ('net income', 'net'),
    ]
    for line in text:
        first = line.split(',')[0].strip().strip('"').lower()
        v = last_num(line)
        if v is None:
            continue
        for needle, key in label_map:
            if first == needle or first.startswith(needle):
                out.setdefault(key, v)
    for k in ('cogs', 'other_income', 'other_expenses'):
        out.setdefault(k, 0.0)

    if strict:
        for req in ('income', 'expenses'):
            if req not in out:
                raise ValueError(f"load_pnl: could not find '{req}' total in {os.path.basename(path)}")
    calc = out.get('income', 0) - out.get('cogs', 0) - out.get('expenses', 0) \
           + out.get('other_income', 0) - out.get('other_expenses', 0)
    out['_crossfoot_net_calc'] = round(calc, 2)
    if 'net' in out:
        out['_crossfoot_ok'] = abs(calc - out['net']) <= 1.00
    else:
        # No 'Net Income' line found (possibly a truncated export) — cannot confirm the foot.
        out['_crossfoot_ok'] = None
        out['_crossfoot_warning'] = "no 'Net Income' line found in the P&L export — cross-foot unverified"
    out['__source'] = os.path.basename(path)
    return out


def load_balance_sheet(path):
    """Parse a QBO Balance Sheet CSV into {label: amount} plus convenience keys.

    QBO's default export is a 'Balance Sheet Comparison' with TWO value columns: the
    current year FIRST and the prior year SECOND. We take the FIRST numeric cell on each
    line (the current year) — taking the last would silently read prior-year figures.
    Surfaces control-flag accounts (Opening Balance Equity, Reconciliation Discrepancies)."""
    out, flags = {}, []
    multi_col = False
    for line in open(path, encoding='utf-8', errors='replace').read().splitlines():
        nums = re.findall(r'\(?-?\$?[\d,]+\.\d{2}\)?', line)
        if not nums:
            continue
        if len(nums) > 1:
            multi_col = True
        label = line.split(',')[0].strip().strip('"')
        val = to_num(nums[0])  # current-year column (first), never prior-year (last)
        if label:
            out.setdefault(label, val)
        low = label.lower().strip()
        if low in ('total assets', 'total for assets'):
            out['total_assets'] = val
        if low in ('total liabilities', 'total for liabilities'):
            out['total_liabilities'] = val
        if any(fa.lower() in low for fa in CONTROL_FLAG_ACCOUNTS) and abs(val) > 0.005:
            flags.append({'account': label, 'amount': val})
    out['_control_flags'] = flags
    out['_comparison_export'] = multi_col   # True => current-year column was used (first of two)
    out['__source'] = os.path.basename(path)
    return out


# --------------------------------------------------------------------------- #
# Reconciliation: book vs real (per account)
# --------------------------------------------------------------------------- #
def reconcile_card(register_paths, real_csv_path, year, real_schema='capital_one',
                   statement_balance_eoy=None, carry_in_prior_year=0.0, is_credit_card=True):
    """Book (QBO registers) vs real (bank download) for one real card account.

    Returns BOTH:
      * charge-flow phantom  = book P&L charges (in-year) - real charges (in-year)
                               -> the duplicate EXPENSE to remove on the P&L.
      * balance phantom      = book ending balance (as of Dec 31) - real statement balance
                               -> the headline liability overstatement; decomposes into
                                  carry_in (prior year) + added_in_year.
    """
    book_gross = book_pl = book_ending = 0.0
    off_before = off_after = 0       # prior-period rows vs later-period rows (both informational)
    off_before_amt = off_after_amt = 0.0
    for p in register_paths:
        df, _hdr = load_qbo_register(p)
        iny, off = split_period(df, year)
        before = off[off['Date'].dt.year < year]
        after = off[off['Date'].dt.year > year]
        off_before += len(before); off_after += len(after)
        off_before_amt += float(before['Charge'].sum(skipna=True))
        off_after_amt += float(after['Charge'].sum(skipna=True))
        book_gross += float(iny['Charge'].sum(skipna=True))
        book_pl += float(iny[~iny['Account'].isin(PL_EXCLUDE)]['Charge'].sum(skipna=True))
        book_ending += register_asof_balance(df, year)

    # Credit-card registers store the running balance as a NEGATIVE liability; statement balances
    # are quoted as positive "amount owed", so we compare magnitudes. A bank/asset account keeps
    # its sign. An unexpectedly-positive credit-card balance is surfaced, not silently abs()-ed.
    sign_flag = None
    if is_credit_card:
        if book_ending > 0.005:
            sign_flag = f"unexpected positive/credit balance ${book_ending:,.2f} on a credit-card account — review the register sign"
        book_liability = round(abs(book_ending), 2)
    else:
        book_liability = round(book_ending, 2)

    real_charge = real_pay = 0.0
    if real_csv_path and os.path.exists(real_csv_path):
        real = load_real_card(real_csv_path, real_schema)
        ry = real[real['_date'].dt.year == year]
        real_charge = float(ry['_debit'].sum())
        real_pay = float(ry['_credit'].sum())

    res = {
        'year': year,
        'book_charges_gross': round(book_gross, 2),
        'book_charges_pl': round(book_pl, 2),
        'real_charges': round(real_charge, 2),
        'real_payments': round(real_pay, 2),
        'phantom_charges_gross': round(book_gross - real_charge, 2),
        'phantom_charges_pl': round(book_pl - real_charge, 2),
        'book_ending_balance': book_liability,
        'off_period_priorperiod_rows': off_before,
        'off_period_future_rows': off_after,
        'off_period_priorperiod_charge_total': round(off_before_amt, 2),
        'off_period_future_charge_total': round(off_after_amt, 2),
        'sign_flag': sign_flag,
    }
    if statement_balance_eoy is not None:
        bp = round(book_liability - statement_balance_eoy, 2)
        res['real_statement_balance'] = round(statement_balance_eoy, 2)
        res['balance_phantom'] = bp
        res['balance_phantom_carry_in'] = round(carry_in_prior_year, 2)
        res['balance_phantom_added_in_year'] = round(bp - carry_in_prior_year, 2)
    return res


# --------------------------------------------------------------------------- #
# Duplicate PAIRING (the defensible delete-set)
# --------------------------------------------------------------------------- #
_STOP = {'the', 'ltd', 'and', 'for', 'inc', 'llc', 'co', 'pet', 'limassol'}


def _tokens(s):
    s = re.sub(r'[^a-z0-9 ]', ' ', str(s).lower())
    return set(w for w in s.split() if len(w) > 2 and w not in _STOP)


def _payee_sim(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def pair_duplicates(dupe_register_path, feed_register_path, feed_card, year,
                    date_window_days=8, amount_pct=0.06, amount_abs=3.0, payee_min_similarity=0.30,
                    control_total=None):
    """Pair each MANUAL re-keyed entry in the inflated (dupe) account to its bank-feed original
    by date(+/- window) + payee/memo fuzzy match + near-amount (FX tolerance band).

    Re-keyed duplicates are NOT penny-identical, so matching is fuzzy by design.
    Returns (pairs_df, summary). Unmatched manual entries are NOT assumed to be duplicates —
    many are the SOLE legitimate booking of a real charge. They are listed for human review.
    """
    ddf, _ = load_qbo_register(dupe_register_path)
    fdf, _ = load_qbo_register(feed_register_path)
    ddf = ddf[ddf['Date'].dt.year == year]
    fdf = fdf[fdf['Date'].dt.year == year].copy().reset_index(drop=True)
    man = ddf[ddf['AddedIn'].astype(str).str.contains('Manual', case=False, na=False)].copy().reset_index(drop=True)

    # Build ALL feasible (manual, feed) candidate pairs, then assign best-score-first
    # (global greedy). This recovers more true pairs than per-row greedy because a feed
    # row is given to its strongest claimant, not the first manual row that scans it.
    candidates = []
    for mi, r in man.iterrows():
        for fi, f in fdf.iterrows():
            dd = abs((r['Date'] - f['Date']).days)
            if dd > date_window_days:
                continue
            ca, cf = float(r['Charge'] or 0), float(f['Charge'] or 0)
            if cf == 0 or abs(ca - cf) > max(amount_abs, amount_pct * cf):
                continue
            ps = max(_payee_sim(r['Payee'], f['Payee']), _payee_sim(r['Memo'], f['Memo']),
                     _payee_sim(r['Payee'], f['Memo']), _payee_sim(r['Memo'], f['Payee']))
            if ps < payee_min_similarity:
                continue
            score = ps - 0.02 * dd - abs(ca - cf) / max(cf, 1)
            candidates.append((score, mi, fi, dd, cf, ps))
    candidates.sort(reverse=True, key=lambda x: x[0])
    used_m, used_f, match_of = set(), set(), {}
    for score, mi, fi, dd, cf, ps in candidates:
        if mi in used_m or fi in used_f:
            continue
        used_m.add(mi); used_f.add(fi); match_of[mi] = (dd, cf, ps)

    rows = []
    for mi, r in man.iterrows():
        refs_feed = bool(re.search(r'-\s*' + re.escape(str(feed_card)), str(r['Memo'])))
        base = {'Date': r['Date'].date(), 'Payee': r['Payee'], 'Memo': str(r['Memo'])[:60],
                'Category': r['Account'], 'book_amount': round(float(r['Charge'] or 0), 2),
                'memo_refs_feed': refs_feed, '__source': r['__source'], '__row': r['__row']}
        if mi in match_of:
            dd, cf, ps = match_of[mi]
            tier = 1 if (refs_feed or ps >= 0.6) else (2 if ps >= 0.4 else 3)
            base.update({'feed_amount': round(cf, 2), 'fx_delta': round(float(r['Charge'] or 0) - cf, 2),
                         'days_apart': dd, 'payee_sim': round(ps, 2), 'Tier': tier})
        else:
            base.update({'feed_amount': None, 'fx_delta': None, 'days_apart': None,
                         'payee_sim': None, 'Tier': 0})
        rows.append(base)
    pairs = pd.DataFrame(rows)
    matched = pairs[pairs['Tier'] > 0]
    matched_sum = round(float(matched['book_amount'].sum()), 2)
    summary = {
        'manual_entries': int(len(man)),
        'matched_as_duplicate': int(len(matched)),
        'matched_duplicate_total': matched_sum,
        'unmatched_keep_or_investigate': int((pairs['Tier'] == 0).sum()),
        'tier1': int((pairs['Tier'] == 1).sum()), 'tier1_total': round(float(pairs[pairs['Tier'] == 1]['book_amount'].sum()), 2),
        'tier2': int((pairs['Tier'] == 2).sum()), 'tier3': int((pairs['Tier'] == 3).sum()),
    }
    if control_total is not None:
        summary['control_total_phantom_pl'] = round(control_total, 2)
        summary['coverage_pct'] = round(100.0 * matched_sum / control_total, 1) if control_total else None
        summary['residual_unmatched_phantom'] = round(control_total - matched_sum, 2)
        summary['note'] = ("The control total (book P&L charges - real charges) is EXACT. The matched-pair "
                           "total is a tiered ESTIMATE for line-by-line review. Residual = investigate; "
                           "do NOT bulk-delete unmatched manual entries (some are the sole legit booking).")
    return pairs, summary


# --------------------------------------------------------------------------- #
# Bridge: entity-type-aware (NEVER plugs a contribution for a for-profit)
# --------------------------------------------------------------------------- #
def bridge(income, expenses, duplicate_expense, funding_model='break_even',
           net_income_target=0, tolerance=1.00):
    """Decompose the corrected net.

    break_even (e.g. a contribution-funded 501(c)(3) sized to net $0):
        loss          = expenses - income
        owner_contrib = loss - duplicate_expense        (the under-recorded contribution)
        corrected_net = (income + owner_contrib) - (expenses - duplicate_expense)  == target
      If owner_contrib comes out NEGATIVE, the break-even assumption is wrong for this entity —
      we FLAG it instead of inventing a negative contribution.

    for_profit (e.g. an S-corp with a real nonzero net):
        We remove only PROVEN duplicate expense. The remaining result is the REAL net — we do
        NOT plug anything. Any unexplained variance is surfaced for human classification.
    """
    income = float(income or 0); expenses = float(expenses or 0); duplicate_expense = float(duplicate_expense or 0)
    booked_net = round(income - expenses, 2)
    corrected_expenses = round(expenses - duplicate_expense, 2)
    base = {'funding_model': funding_model, 'booked_income': round(income, 2),
            'booked_expenses': round(expenses, 2), 'booked_net': booked_net,
            'duplicate_expense_removed': round(duplicate_expense, 2),
            'corrected_expenses': corrected_expenses}

    if funding_model == 'break_even':
        loss = round(expenses - income, 2)
        owner_contrib = round(loss - duplicate_expense, 2)
        corrected_income = round(income + owner_contrib, 2)
        corrected_net = round(corrected_income - corrected_expenses, 2)
        base.update({'loss': loss, 'owner_contrib_to_record': owner_contrib,
                     'corrected_income': corrected_income, 'corrected_net': corrected_net,
                     'net_income_target': net_income_target})
        if owner_contrib < -tolerance:
            base['WARNING'] = ("Break-even assumption FAILED: implied owner contribution is negative "
                               f"(${owner_contrib:,.2f}). This entity is not break-even — use funding_model: for_profit.")
        base['ties_to_target'] = abs(corrected_net - (net_income_target or 0)) <= tolerance
        return base

    # for_profit
    corrected_net = round(income - corrected_expenses, 2)
    base.update({'corrected_income': round(income, 2), 'corrected_net': corrected_net,
                 'net_income_target': None,
                 'unexplained_variance': ("Residual after removing proven duplicate expense is the REAL net. "
                                          "Do NOT plug. Reconcile each account's balance phantom to 0 and classify "
                                          "any remaining variance (income vs expense vs reclass) by hand.")})
    return base
