#!/usr/bin/env bash
#
# Start the rig's emulator in the background on its own socket.
#
# The whole start is one lifecycle transaction under the shared guest lock, so
# two concurrent starts cannot create two instances on one disk; the second
# waits, then sees the first already answering. The emulator never inherits
# the lock fd or the re-entrancy marker.
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

# A live emulator without a socket (mid-shutdown, or a socket removed by hand)
# is still an instance: only a pid proven ours counts.
rig_instance_pid() {
	local pid=
	if [ -f "$RIG_PID_FILE" ]; then
		pid=$(cat "$RIG_PID_FILE" 2>/dev/null || true)
	fi
	if [ -n "$pid" ] && rig_pid_is_ours "$pid" && rig_pid_alive "$pid"; then
		printf '%s\n' "$pid"
	fi
}

start_rig() {
	rig_require_iris
	[ -f "$RIG_CONFIG" ] || rig_die "no config at $RIG_CONFIG (run scripts/rig/new-rig.sh)"
	mkdir -p "$RIG_LOG_DIR"

	local running_pid
	if rig_running; then
		rig_log "already running on $RIG_SOCKET"
		exit 0
	fi
	running_pid=$(rig_instance_pid)
	if [ -n "$running_pid" ]; then
		rig_log "already running (verified pid $running_pid) on $RIG_SOCKET"
		exit 0
	fi
	[ -S "$RIG_SOCKET" ] && rm -f "$RIG_SOCKET"

	local -a args=(--config "$RIG_CONFIG" --ci --serial-log "$RIG_SERIAL_LOG")
	[ -n "$headless" ] && args+=(--headless)

	rig_log "starting $RIG_IRIS ${args[*]}"
	if [ -n "$foreground" ]; then
		# Foreground is a manual debug mode: release the lock before handing
		# the terminal to iris, or the emulator would hold it for its lifetime.
		# The verified-pid check above still refuses a second start.
		unset RIG_GUEST_LOCK_HELD
		exec 9>&-
		exec "$RIG_IRIS" "${args[@]}"
	fi

	# The child must not inherit the lock fd: if this shell died uncleanly the
	# emulator would otherwise hold the guest lock for its whole lifetime.
	unset RIG_GUEST_LOCK_HELD
	if [ -n "${RIG_PROC_TITLE:-}" ]; then
		# Per-task instances (task-rig.sh) set RIG_PROC_TITLE so `ps` names
		# the emulator after the thing it is rebuilding. exec -a sets argv[0]
		# without disturbing the real --config/--ci iris parses. The bash
		# layer execs straight into iris, so $! is still the emulator's pid.
		setsid bash -c 'exec -a "$0" "$@"' "$RIG_PROC_TITLE" "$RIG_IRIS" "${args[@]}" \
			>>"$RIG_STDOUT_LOG" 2>&1 </dev/null 9>&- &
	else
		setsid "$RIG_IRIS" "${args[@]}" >>"$RIG_STDOUT_LOG" 2>&1 </dev/null 9>&- &
	fi
	echo $! >"$RIG_PID_FILE"

	local _
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
}

# The whole start is one lifecycle transaction; nested calls from provision or
# oracle re-enter through RIG_GUEST_LOCK_HELD instead of deadlocking.
rig_with_guest_lock start_rig
