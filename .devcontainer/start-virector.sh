#!/usr/bin/env bash
set -euo pipefail

cd "${CODESPACE_VSCODE_FOLDER:-$(pwd)}"

pid_file="/tmp/virector-uvicorn.pid"
log_file="/tmp/virector-uvicorn.log"

if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
  echo "Virector is already running at http://localhost:8000/studio/"
  exit 0
fi

nohup python -m uvicorn virector.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  >"$log_file" 2>&1 &
echo $! >"$pid_file"

echo "Virector is starting at http://localhost:8000/studio/"
echo "Server log: $log_file"
