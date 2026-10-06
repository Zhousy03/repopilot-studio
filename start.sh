#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'Create environment first: python3 -m venv .venv' 'Then: .venv/bin/python -m pip install -e ".[ai,dev]"'
  exit 1
fi
exec .venv/bin/python -m uvicorn repopilot.api:app --host 127.0.0.1 --port 8000
