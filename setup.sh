#!/usr/bin/env bash
# One-time setup: build a self-contained Python environment for the close scripts.
# Safe to re-run. Creates ./venv next to this script and installs requirements.txt.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
echo "Using $($PY --version) at $(command -v "$PY")"

if [ ! -d venv ]; then
  echo "Creating virtual environment in ./venv ..."
  "$PY" -m venv venv
fi

echo "Installing dependencies (this can take a couple of minutes the first time) ..."
./venv/bin/python -m pip install --quiet --upgrade pip
./venv/bin/python -m pip install --quiet -r requirements.txt

echo
echo "Verifying imports ..."
./venv/bin/python - <<'PY'
import pandas, openpyxl, xlrd, numbers_parser, yaml, pdfplumber, docx
print("OK — all dependencies importable.")
PY
echo
echo "Done. Run a close with:  ./close.sh --help"
