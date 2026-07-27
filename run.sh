#!/usr/bin/env bash
# Launch the AI Usage Dashboard. Any extra args pass through to dashboard.py
# (e.g. ./run.sh --port 9000 --rebuild). Requires python3 (stdlib only).
set -euo pipefail
cd "$(dirname "$0")"
exec python3 dashboard.py "$@"
