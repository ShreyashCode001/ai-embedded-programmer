#!/usr/bin/env bash
# One-time setup on Linux/macOS.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python scripts/setup_app.py "$@"
