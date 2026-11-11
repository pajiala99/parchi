#!/usr/bin/env bash
# One-command offline demo: no AWS account needed.
#
#   scripts/demo.sh            # set up if needed, build, run, open http://localhost:3001
#   scripts/demo.sh --port 3005
#
# Runs the real Lambda handlers against an AWS emulator (moto: S3, SQS, SNS, DynamoDB)
# and serves the built dashboard from the same port. Ctrl+C stops everything.
# Needs Python 3.12+ and Node.js 20+. Tesseract (brew install tesseract) gives real OCR;
# without it, only the sample bills can be read.
set -euo pipefail

PORT=3001
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p | --port) PORT="$2"; shift 2 ;;
    -h | --help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "demo: unknown option $1" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python_bin() {
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
      echo "$candidate"; return
    fi
  done
  echo "demo: Python 3.12 or newer is needed" >&2; exit 1
}

if [[ ! -x .venv/bin/python ]]; then
  echo "==> Creating the Python environment (first run only)"
  "$(python_bin)" -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r backend/requirements-dev.txt
fi

command -v npm >/dev/null || { echo "demo: Node.js (npm) is needed" >&2; exit 1; }
if [[ ! -d frontend/node_modules ]]; then
  echo "==> Installing dashboard packages (first run only)"
  (cd frontend && npm ci --silent)
fi

echo "==> Building the dashboard"
(cd frontend && npm run build --silent >/dev/null)

if command -v tesseract >/dev/null; then
  echo "==> OCR: Tesseract (any bill you upload is read for real)"
else
  echo "==> OCR: replay engine (install Tesseract to read your own bills: brew install tesseract)"
fi

URL="http://localhost:$PORT"
( # open the browser once the API answers
  for _ in $(seq 1 60); do
    if curl -sf "$URL/api/health" >/dev/null; then
      if [[ -z "${PARCHI_NO_OPEN:-}" ]] && command -v open >/dev/null; then open "$URL"; else echo "Open $URL in your browser"; fi
      exit 0
    fi
    sleep 0.5
  done
) &

exec .venv/bin/python local/devstack.py --api-port "$PORT" --app-url "$URL"
