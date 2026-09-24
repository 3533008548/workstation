#!/usr/bin/env bash
# Contract gate: regenerate JSON Schema, fail on drift, then run the tests.
# Run from repo root:  bash scripts/verify.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve interpreter: $PYTHON > repo .venv > bare python
if [[ -n "${PYTHON:-}" ]]; then
  PY="$PYTHON"
elif [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
  PY="$ROOT/.venv/Scripts/python.exe"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python"
fi

echo "==> regenerating schemas"
"$PY" "$ROOT/scripts/gen_schema.py" >/dev/null

if [[ -n "$(git -C "$ROOT" status --porcelain -- contracts/schema)" ]]; then
  echo "ERROR: contracts/schema is out of date. Commit the regenerated files."
  git -C "$ROOT" status --short -- contracts/schema
  exit 1
fi
echo "==> schemas clean"

# Full suite from the repo root: root pyproject testpaths covers both
# contracts/python/tests and tests (gateway + skills). Running only the
# contract subset would let an adapter regression through the gate.
echo "==> running tests (contracts + gateway + skills)"
( cd "$ROOT" && "$PY" -m pytest )

echo "==> OK"
