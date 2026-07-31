#!/usr/bin/env bash
set -euo pipefail

cd "${CODESPACE_VSCODE_FOLDER:-$(pwd)}"

backend_pid_file="/tmp/virector-uvicorn.pid"
backend_log_file="/tmp/virector-uvicorn.log"
web_pid_file="/tmp/virector-web.pid"
web_log_file="/tmp/virector-web.log"

if [[ ! -f "$backend_pid_file" ]] || ! kill -0 "$(<"$backend_pid_file")" 2>/dev/null; then
  nohup python -m uvicorn virector.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    >"$backend_log_file" 2>&1 &
  echo $! >"$backend_pid_file"
fi

if [[ ! -f "$web_pid_file" ]] || ! kill -0 "$(<"$web_pid_file")" 2>/dev/null; then
  nohup npm --prefix web run dev -- --hostname 0.0.0.0 \
    >"$web_log_file" 2>&1 &
  echo $! >"$web_pid_file"
fi

echo "Virector Web is starting at http://localhost:3000/"
echo "Backend log: $backend_log_file"
echo "Web log: $web_log_file"
