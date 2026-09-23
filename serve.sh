#!/usr/bin/env bash
# Static file server for ~/Desktop/mytools, wired to the `myserver` shell command.
#
#   myserver           start (if needed) and open the index page
#   myserver stop      stop the server
#   myserver status    show whether it is running
#   myserver restart   stop, then start again
#   myserver log       tail the request log
#
# Override the port with MYSERVER_PORT=9000 myserver
# Override which files the read API may reach with MYSERVER_ROOT=/some/dir (default: $HOME)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/.venv/bin/python3"
PORT="${MYSERVER_PORT:-8888}"
URL="http://localhost:${PORT}/index.html"
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/myserver"
PID_FILE="${STATE_DIR}/server-${PORT}.pid"
LOG_FILE="${STATE_DIR}/server-${PORT}.log"

mkdir -p "$STATE_DIR"

# Is our recorded PID still a live process?
running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null)" || return 1
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

# Is anything at all listening on the port? (e.g. a server started by hand)
# `ss` is Linux-only; macOS has neither ss nor iproute2, so fall back to lsof.
port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH "sport = :${PORT}" 2>/dev/null | grep -q .
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null
  fi
}

open_browser() {
  if [[ "$(uname -s)" == "Darwin" ]] && command -v open >/dev/null; then
    open "$URL" >/dev/null 2>&1 &
  elif [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v xdg-open >/dev/null; then
    xdg-open "$URL" >/dev/null 2>&1 &
  else
    echo "No display detected — open $URL yourself."
  fi
}

start() {
  if [[ ! -x "$PYTHON" ]]; then
    echo "myserver: no venv at $ROOT/.venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    return 1
  fi
  if running; then
    echo "myserver: already running on port ${PORT} (pid $(cat "$PID_FILE"))"
  elif port_busy; then
    # Something else owns the port; don't try to take it over.
    echo "myserver: port ${PORT} is already in use by another process."
    echo "          Opening ${URL} anyway — or pick another port with MYSERVER_PORT=9000 myserver"
  else
    nohup "$PYTHON" "$ROOT/server.py" "$PORT" >>"$LOG_FILE" 2>&1 &
    local pid=$!
    disown "$pid" 2>/dev/null
    echo "$pid" >"$PID_FILE"

    # Give it a moment, then confirm it actually came up.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      port_busy && break
      sleep 0.2
    done
    if ! running || ! port_busy; then
      echo "myserver: failed to start — see $LOG_FILE" >&2
      rm -f "$PID_FILE"
      return 1
    fi
    echo "myserver: serving ${ROOT} at http://localhost:${PORT} (pid ${pid})"
    echo "          stop with: myserver stop"
  fi
  open_browser
}

stop() {
  if running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null
    rm -f "$PID_FILE"
    echo "myserver: stopped (pid ${pid})"
  else
    rm -f "$PID_FILE"
    echo "myserver: not running"
  fi
}

case "${1:-start}" in
  start|"")  start ;;
  stop)      stop ;;
  restart)   stop; start ;;
  status)
    if running; then
      echo "myserver: running on port ${PORT} (pid $(cat "$PID_FILE")) serving ${ROOT}"
    elif port_busy; then
      echo "myserver: not started by us, but port ${PORT} is in use"
    else
      echo "myserver: not running"
    fi
    ;;
  open)      open_browser ;;
  log)       tail -n 40 -f "$LOG_FILE" ;;
  *)
    echo "usage: myserver [start|stop|restart|status|open|log]" >&2
    exit 2
    ;;
esac
