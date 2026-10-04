#!/usr/bin/env bash
#
# Stop the rig's emulator cleanly (CI `quit`, then SIGTERM, then SIGKILL).
#
# Usage: scripts/rig/stop-rig.sh [--timeout SECONDS]
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
	cat <<'EOF'
Usage: scripts/rig/stop-rig.sh [--timeout SECONDS]

  --timeout S  seconds to wait for a clean quit before signalling (default 60)
  -h, --help   show this help
EOF
}

timeout=60
while [ $# -gt 0 ]; do
	case "$1" in
		--timeout) timeout=$2; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) rig_die "unknown option: $1 (try --help)" ;;
	esac
done

if ! [ -S "$RIG_SOCKET" ]; then
	rig_log "not running (no socket at $RIG_SOCKET)"
	exit 0
fi

rig_log "asking iris to quit"
ic quit >/dev/null 2>&1 || true

pid=
[ -f "$RIG_PID_FILE" ] && pid=$(cat "$RIG_PID_FILE")
for _ in $(seq 1 "$timeout"); do
	[ -S "$RIG_SOCKET" ] || break
	sleep 1
done
[ -S "$RIG_SOCKET" ] && rm -f "$RIG_SOCKET"

if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
	rig_log "sending SIGTERM to $pid"
	kill "$pid" 2>/dev/null || true
	sleep 5
fi
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
	rig_log "sending SIGKILL to $pid"
	kill -9 "$pid" 2>/dev/null || true
fi

rm -f "$RIG_PID_FILE"
rig_log "stopped"
