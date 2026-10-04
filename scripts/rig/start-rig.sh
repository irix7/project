#!/usr/bin/env bash
#
# Start the rig's emulator in the background on its own socket.
#
# --headless is only for the NVRAM-seeding phase: it keeps the graphics board
# unmapped so the PROM's console is serial. The install and every later boot
# run with REX3 mapped (headless = false in the config, no --headless here), so
# miniroot's hinv reports a graphics board and inst keeps the X software.
#
# Usage: scripts/rig/start-rig.sh [--headless] [--foreground]
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
	cat <<'EOF'
Usage: scripts/rig/start-rig.sh [--headless] [--foreground]

  --headless    run with no graphics board mapped (NVRAM seeding only)
  --foreground  run iris in the foreground instead of backgrounding it
  -h, --help    show this help
EOF
}

headless= foreground=
while [ $# -gt 0 ]; do
	case "$1" in
		--headless) headless=1; shift ;;
		--foreground) foreground=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) rig_die "unknown option: $1 (try --help)" ;;
	esac
done

rig_require_iris
[ -f "$RIG_CONFIG" ] || rig_die "no config at $RIG_CONFIG (run scripts/rig/new-rig.sh)"
mkdir -p "$RIG_LOG_DIR"

if rig_running; then
	rig_log "already running on $RIG_SOCKET"
	exit 0
fi
[ -S "$RIG_SOCKET" ] && rm -f "$RIG_SOCKET"

args=(--config "$RIG_CONFIG" --ci --serial-log "$RIG_SERIAL_LOG")
[ -n "$headless" ] && args+=(--headless)

rig_log "starting $RIG_IRIS ${args[*]}"
if [ -n "$foreground" ]; then
	exec "$RIG_IRIS" "${args[@]}"
fi

setsid "$RIG_IRIS" "${args[@]}" >>"$RIG_STDOUT_LOG" 2>&1 </dev/null &
echo $! >"$RIG_PID_FILE"

for _ in $(seq 1 60); do
	if rig_running; then
		rig_log "up (pid $(cat "$RIG_PID_FILE")) on $RIG_SOCKET"
		exit 0
	fi
	sleep 1
done

rig_log "iris did not answer on $RIG_SOCKET; see $RIG_STDOUT_LOG"
tail -20 "$RIG_STDOUT_LOG" >&2 || true
exit 1
