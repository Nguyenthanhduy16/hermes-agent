#!/usr/bin/env bash
# Gateway of the isolated benchmark profile (.runtime/hermes-home/profiles/bench, or BENCH_PROFILE).
# It runs beside the Telegram bot's gateway (default profile) and never touches it.
#
#   scripts/bench-gateway.sh start <policy> <label> [VAR=value ...]
#   scripts/bench-gateway.sh stop
#   scripts/bench-gateway.sh status
#
# <policy>: always-strong | always-cheap | random | rule | <registered policy>
# Extra VAR=value pairs go to the gateway environment, e.g. ASSISTANT_ROUTER_STRONG_SHARE=0.25.
# Before starting: copies src/assistant_core into the profile's plugin (Hermes loads that copy,
# not src/), then waits for the API port and checks that telemetry recorded this policy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${BENCH_PROFILE:-$ROOT/.runtime/hermes-home/profiles/bench}"
PY="$ROOT/.runtime/venv/bin/python"
LOG="$PROFILE/logs/bench-gateway.log"
PORT="$(grep -E '^API_SERVER_PORT=' "$PROFILE/.env" | cut -d= -f2- || true)"
PORT="${PORT:-8650}"

bench_pid() {
    # PID of a live gateway whose pid file names THIS profile; nothing otherwise.
    "$PY" - "$PROFILE" <<'EOF'
import json, os, sys
from pathlib import Path
home = Path(sys.argv[1])
try:
    record = json.loads((home / "gateway.pid").read_text())
    pid = int(record["pid"])
    if Path(record.get("hermes_home", "")).resolve() != home.resolve():
        sys.exit(0)
    os.kill(pid, 0)
    print(pid)
except (OSError, ValueError, KeyError):
    pass
EOF
}

stop() {
    local pid
    pid="$(bench_pid)"
    if [ -z "$pid" ]; then
        echo "bench gateway: not running"
        return 0
    fi
    kill -TERM "$pid"
    for _ in $(seq 1 60); do
        kill -0 "$pid" 2>/dev/null || { echo "bench gateway: stopped ($pid)"; return 0; }
        sleep 1
    done
    echo "bench gateway: $pid did not stop within 60 s" >&2
    return 1
}

start() {
    local policy="${1:?policy required}" label="${2:?label required}"
    shift 2
    stop
    "$PY" - "$ROOT" "$PROFILE" <<'EOF'
import sys
from pathlib import Path
root, profile = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / "scripts"))
import configure
configure.install_plugin(profile, "assistant-core", root / "plugins" / "assistant-core", "assistant_core")
EOF
    diff -rq --exclude=__pycache__ "$ROOT/src/assistant_core" "$PROFILE/plugins/assistant-core/assistant_core" >/dev/null \
        || { echo "plugin copy differs from src/assistant_core" >&2; return 1; }
    local since
    since="$(date +%s)"
    (
        export HERMES_HOME="$PROFILE" ASSISTANT_ROUTER_POLICY="$policy" ASSISTANT_RUN_LABEL="$label"
        for pair in "$@"; do export "$pair"; done
        printf '\n==== %s start policy=%s label=%s %s\n' "$(date '+%F %T')" "$policy" "$label" "$*" >> "$LOG"
        nohup hermes gateway run >> "$LOG" 2>&1 &
    )
    for _ in $(seq 1 60); do
        if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
            break
        fi
        sleep 1
    done
    "$PY" - "$PROFILE" "$policy" "$label" "$since" <<'EOF'
import sqlite3, sys, time
home, policy, label, since = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
db = sqlite3.connect(f"{home}/assistant-core/telemetry.sqlite3")
for _ in range(30):
    row = db.execute("SELECT r.run_id, r.label, rr.policy FROM runs r LEFT JOIN router_runs rr USING (run_id) "
                     "WHERE r.started_at >= ? ORDER BY r.started_at DESC LIMIT 1", (since - 1,)).fetchone()
    if row:
        break
    time.sleep(1)
if not row or row[1] != label or (row[2] or "off") != policy:
    sys.exit(f"telemetry did not record this start (got {row}); see the gateway log")
print(f"bench gateway: telemetry run {row[0]} label={row[1]} policy={row[2]}")
EOF
    echo "bench gateway: listening on 127.0.0.1:$PORT (pid $(bench_pid)); log $LOG"
}

case "${1:-}" in
    start) shift; start "$@" ;;
    stop) stop ;;
    status) pid="$(bench_pid)"; [ -n "$pid" ] && echo "bench gateway: running ($pid)" || echo "bench gateway: not running" ;;
    *) sed -n 2,12p "$0"; exit 2 ;;
esac
