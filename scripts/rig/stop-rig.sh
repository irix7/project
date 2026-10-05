#!/usr/bin/env bash
#
# Stop the rig's emulator: CI `quit`, then a verified SIGTERM/SIGKILL.
#
# The saved PID is never trusted on `kill -0` alone. It is signalled only
# after rig_pid_is_ours proves /proc/PID/exe is this rig's iris and its
# cmdline carries this rig's --config and --ci. A stale or recycled PID is
# refused with a clear message and nothing is signalled; the socket is left
# in place rather than removed out from under an unverified process. See
# docs/rig.md, "Process ownership".
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

# Poll quickly after a signal so a cooperative process is noticed at once;
# the iteration count bounds the total wait to five seconds.
rig_wait_dead() {
	local pid=$1
	local _
	for _ in $(seq 1 50); do
		rig_pid_alive "$pid" || return 0
		sleep 0.1
	done
	return 1
}

stop_rig() {
	local pid= pid_verified=0

	if [ -f "$RIG_PID_FILE" ]; then
		pid=$(cat "$RIG_PID_FILE" 2>/dev/null || true)
	fi
	if [ -n "$pid" ] && rig_pid_is_ours "$pid"; then
		pid_verified=1
	fi

	if [ -S "$RIG_SOCKET" ]; then
		rig_log "asking iris to quit"
		ic quit >/dev/null 2>&1 || true
		local _
		for _ in $(seq 1 "$timeout"); do
			[ -S "$RIG_SOCKET" ] || break
			sleep 1
		done
	fi

	# Signal only a PID verified as this rig's emulator: SIGTERM, then, if it
	# survives, SIGKILL. A stale/recycled PID never reaches these branches.
	if [ "$pid_verified" -eq 1 ] && rig_pid_alive "$pid"; then
		rig_log "sending SIGTERM to verified pid $pid"
		rig_signal "$pid" TERM
		rig_wait_dead "$pid" || true
	fi
	if [ "$pid_verified" -eq 1 ] && rig_pid_alive "$pid"; then
		rig_log "still alive; sending SIGKILL to verified pid $pid"
		rig_signal "$pid" KILL
		rig_wait_dead "$pid" || true
	fi

	# A verified emulator that survived SIGKILL is never reported stopped,
	# with or without a socket: the caller must not go on to delete disks or
	# NVRAM under a live process.
	if [ "$pid_verified" -eq 1 ] && rig_pid_alive "$pid"; then
		rig_log "verified pid $pid is still alive after SIGKILL"
		rig_log "leaving socket and pid file in place for inspection"
		exit 1
	fi

	if [ -S "$RIG_SOCKET" ]; then
		if [ "$pid_verified" -eq 1 ]; then
			rig_log "removing stale socket $RIG_SOCKET (verified pid $pid exited)"
			rm -f "$RIG_SOCKET"
		else
			if [ -n "$pid" ]; then
				rig_log "refusing to signal pid $pid from $RIG_PID_FILE: not this rig's emulator (exe/cmdline check)"
			else
				rig_log "refusing to remove socket $RIG_SOCKET: no verified pid in $RIG_PID_FILE"
			fi
			rig_log "leaving socket and pid file in place for inspection"
			exit 1
		fi
	fi

	if [ -n "$pid" ] && rig_pid_alive "$pid"; then
		# No socket, but a live process that is not ours: it is not the rig's
		# emulator, so the rig is stopped even though the pid file was stale.
		rig_log "note: recorded pid $pid is alive but is not this rig's emulator; left untouched"
	fi

	rm -f "$RIG_PID_FILE"
	rig_log "stopped"
}

# The whole stop is one lifecycle transaction: no start, provision or guest
# client may interleave between the quit and the signal.
rig_with_guest_lock stop_rig
