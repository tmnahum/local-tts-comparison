#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
READROOM_ROOT="$PWD"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/readroom.service" <<EOF
[Unit]
Description=Readroom local article and speech comparison service
After=network.target

[Service]
Type=simple
WorkingDirectory=$READROOM_ROOT
ExecStart=$READROOM_ROOT/scripts/start.sh
Restart=on-failure
RestartSec=3
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now readroom.service
