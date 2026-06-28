#!/usr/bin/env bash
# The one command. Runs the whole close in the local venv.
#   ./close.sh --entity st_gertrude --year 2025 --period "<folder>"
# First run ./setup.sh once to build the environment.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x venv/bin/python ]; then
  echo "No venv yet — running ./setup.sh first..."
  ./setup.sh
fi
exec ./venv/bin/python scripts/close.py "$@"
