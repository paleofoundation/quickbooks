# Test fixtures — REAL data, kept local only

The golden-master regression test (`tests/test_golden_master.py`) runs against the **real** Gardens
of St. Gertrude 2025 exports. Because that is actual financial data (card transactions, QBO registers,
P&L, Balance Sheet), it is **deliberately NOT committed to git** — it is gitignored and was scrubbed
from the repository history. So this repo can be pushed (private or public) without exposing bank data.

To run the golden master on a machine that has the data, place these files here in
`tests/fixtures/st_gertrude_2025/`:

```
5391_Register.xls
7933_Register.xls
Capital One 7933 2025 transaction_download.csv
Gardens of St. Gertrude Inc._Profit and Loss.csv
Gardens of St. Gertrude Inc._Balance Sheet.csv
```

Then run:

```
./venv/bin/python tests/test_golden_master.py
```

With the files present it asserts the hand-verified close to the cent (phantom $47,213.18, duplicate
$35,617.29, owner contribution $6,709.35, corrected net $0). Without them it prints `SKIPPED` and passes.
