#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv-server/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${READROOM_PORT:-28765}"
