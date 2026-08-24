#!/usr/bin/env bash
set -euo pipefail
TICKER="${1:-KOEI}"
python -m zse_tool probe --ticker "$TICKER"
python -m zse_tool list --ticker "$TICKER" --limit 3
python -m zse_tool db-stats
