#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v uv >/dev/null 2>&1; then
  READROOM_UV=$(command -v uv)
else
  if [[ ! -x .bootstrap/bin/uv ]]; then
    python3 -m pip install --target .bootstrap 'uv==0.13.1'
  fi
  READROOM_UV="$PWD/.bootstrap/bin/uv"
fi
"$READROOM_UV" python install 3.11
for name in server tts chatterbox; do
  if [[ ! -x ".venv-$name/bin/python" ]]; then
    "$READROOM_UV" venv --python 3.11 ".venv-$name"
  fi
  "$READROOM_UV" pip sync --python ".venv-$name/bin/python" "requirements-$name.lock" --extra-index-url https://download.pytorch.org/whl/cu124 --index-strategy unsafe-best-match
done
.venv-server/bin/python scripts/download_models.py
printf '\nReady. Run ./scripts/start.sh and open http://localhost:8765\n'
