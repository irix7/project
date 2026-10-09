#!/usr/bin/env bash
#
# Shared environment for the independent IRIX 6.5.7m rig (issue #2, ADR-0004).
#
# The rig lives outside the repo on shared storage: heavy emulator builds and
# disk images must not land on the local root filesystem. Everything is keyed
# off IRIX_RIG_DIR, so a second rig (or a CI host) only needs a different root.
#
# This rig is deliberately independent of the sibling sgi-mame session:
#
#   * its own iris checkout, built from a pinned upstream commit
#   * its own CI socket (the sibling owns /tmp/iris.sock)
#   * its own NVRAM, disks, serial log and config
#
# Nothing here ever connects to or mutates the sibling's files.
#
set -euo pipefail

RIG_REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export RIG_REPO_ROOT

# The project's emulator fork; carries the CI COW-overlay relocation that the
# per-task rigs (task-rig.sh) rely on via IRIS_COW_OVERLAY_DIR.
RIG_IRIS_REPO=${RIG_IRIS_REPO:-https://github.com/irix7/iris}
RIG_IRIS_COMMIT=${RIG_IRIS_COMMIT:-1528d703776cb19338c548888438ee22efc3405b}

# Root of the rig tree. 6.2T is available on the shared volume; the disk image
# is sparse until the install writes to it.
RIG_DIR=${IRIX_RIG_DIR:-/mnt/europa/sgi-toolchain-scratch/rig}
export RIG_DIR

export RIG_IRIS_DIR="$RIG_DIR/iris"
export RIG_IRIS="$RIG_IRIS_DIR/target/release/iris"
export RIG_IRIS_CI="$RIG_IRIS_DIR/target/release/iris-ci"
export RIG_SOCKET="$RIG_DIR/iris.sock"
export RIG_CONFIG="$RIG_DIR/iris.toml"
export RIG_NVRAM="$RIG_DIR/nvram.bin"
export RIG_DISK="$RIG_DIR/disks/irix65.raw"
export RIG_SCRATCH="$RIG_DIR/scratch.raw"
export RIG_MEDIA_DIR="$RIG_DIR/media"
export RIG_ORACLE_DIR="$RIG_DIR/oracle"
export RIG_LOG_DIR="$RIG_DIR/logs"
export RIG_SERIAL_LOG="$RIG_LOG_DIR/serial.log"
export RIG_STDOUT_LOG="$RIG_LOG_DIR/iris-stdout.log"
export RIG_DRIVER_LOG="$RIG_LOG_DIR/driver.log"
export RIG_PID_FILE="$RIG_DIR/iris.pid"
export RIG_STATE_DIR="$RIG_DIR/state"

# Rust toolchain and crate caches local to the rig, so the build does not
# depend on (or contend with) the sibling session's cargo state. Override with
# IRIX_RUSTUP_HOME / IRIX_CARGO_HOME to reuse an existing cache.
export RIG_RUSTUP_HOME=${IRIX_RUSTUP_HOME:-$RIG_DIR/tools/rustup}
export RIG_CARGO_HOME=${IRIX_CARGO_HOME:-$RIG_DIR/tools/cargo}

# Install media, in changer order. The first disc is bootable and carries the
# 6.5.7 miniroot; the rest are the foundations and the second overlay.
RIG_MEDIA_TOOLS_OVERLAYS_1='IRIX 6.5.7 Installation Tools and Overlays (1 of 2).iso'
RIG_MEDIA_FOUNDATION_1='IRIX 6.5 Foundation 1.iso'
RIG_MEDIA_FOUNDATION_2='IRIX 6.5 Foundation 2.iso'
RIG_MEDIA_OVERLAYS_2='IRIX 6.5.7 Overlays (2 of 2).iso'
RIG_INSTALL_MEDIA=(
	"$RIG_MEDIA_TOOLS_OVERLAYS_1"
	"$RIG_MEDIA_FOUNDATION_1"
	"$RIG_MEDIA_FOUNDATION_2"
	"$RIG_MEDIA_OVERLAYS_2"
)

# Oracle media (issue #3) are spelled in scripts/rig/oracle-driver.py's
# ORACLE_SETS, the single list that oracle.sh and status.sh walk; they live in
# $RIG_MEDIA_DIR beside the install media and are never published.

rig_log() {
	printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

rig_die() {
	echo "rig: $*" >&2
	exit 1
}

# Generation-bound state markers (rig-state.py, issue #25). Every marker is
# bound to the boot disk's volume header and the rig config digest, so a
# replaced disk or an edited iris.toml invalidates it; writes are atomic.
# RIG_STATE_DIR, RIG_DISK and RIG_CONFIG are exported above, so the CLI sees
# the same identity the shell does.
rig_state() {
	python3 "$RIG_REPO_ROOT/scripts/rig/rig-state.py" "$@"
}

# True only when NAME's marker exists and certifies the current generation.
rig_state_marked() {
	rig_state marked "$1"
}

# A compiled iris is the precondition of everything else.
rig_require_iris() {
	[ -x "$RIG_IRIS" ] || rig_die "no iris binary at $RIG_IRIS (run scripts/rig/build-iris.sh)"
	[ -x "$RIG_IRIS_CI" ] || rig_die "no iris-ci binary at $RIG_IRIS_CI"
}

# All iris-ci calls address our socket, never the default /tmp/iris.sock.
# Both carriers are set deliberately: --socket survives an inherited
# environment, and IRIS_SOCKET covers any caller that inspects the env first.
# See docs/rig.md, "Socket identity".
ic() {
	IRIS_SOCKET="$RIG_SOCKET" "$RIG_IRIS_CI" --socket "$RIG_SOCKET" "$@"
}

# True when the rig's CI socket answers.
rig_running() {
	[ -S "$RIG_SOCKET" ] && ic ping >/dev/null 2>&1
}

# True when PID is a live process, not merely a numbered zombie. `kill -0`
# succeeds for an unreaped zombie, which would make the stop and fresh paths
# wait on a process that has already exited.
rig_pid_alive() {
	local pid=${1:-} state=
	kill -0 "$pid" 2>/dev/null || return 1
	state=$(cat "/proc/$pid/stat" 2>/dev/null) || return 1
	# stat is `pid (comm) state ...`; comm can contain spaces and parentheses,
	# so strip through the last ')' before reading the state field.
	state=${state##*) }
	state=${state%% *}
	[ "$state" != "Z" ]
}

# True when PID is demonstrably this rig's emulator.
#
# The lifecycle scripts signal on this answer, so any doubt resolves to "not
# ours": a missing or unreadable /proc entry, a non-numeric or non-positive
# PID, or an exe/cmdline that does not name $RIG_IRIS with this rig's
# --config and --ci. A stale or recycled PID therefore cannot be signalled
# by accident. See docs/rig.md, "Process ownership".
rig_pid_is_ours() {
	local pid=${1:-}
	case "$pid" in
		'' | *[!0-9]*) return 1 ;;
	esac
	[ "$pid" -gt 0 ] 2>/dev/null || return 1

	local exe rig_exe
	exe=$(readlink -f "/proc/$pid/exe" 2>/dev/null) || return 1
	rig_exe=$(readlink -f "$RIG_IRIS" 2>/dev/null) || return 1
	[ -n "$exe" ] && [ "$exe" = "$rig_exe" ] || return 1

	local -a argv=()
	local arg
	if [ -r "/proc/$pid/cmdline" ]; then
		while IFS= read -r -d '' arg; do
			argv+=("$arg")
		done <"/proc/$pid/cmdline"
	fi

	local has_ci= has_config= i
	for ((i = 0; i < ${#argv[@]}; i++)); do
		case "${argv[i]}" in
			--ci) has_ci=1 ;;
			--config) [ "${argv[i + 1]:-}" = "$RIG_CONFIG" ] && has_config=1 ;;
		esac
	done
	[ -n "$has_ci" ] && [ -n "$has_config" ]
}

# rig_signal PID SIGNAL: send SIGNAL (a name or number) to PID, never
# failing the caller. RIG_KILL_CMD exists for the fault-injection tests: a
# stub signaller that cannot stop the process proves stop-rig.sh never
# reports a verified emulator stopped while it is still alive.
rig_signal() {
	"${RIG_KILL_CMD:-kill}" "-$2" "$1" 2>/dev/null || true
}

# Conservative quiescence proof for destructive operations. Must be called
# under the guest lock. A socket that is still present, or any live pid in the
# pid file (ours or not: identity cannot always be proven), means the caller
# must not delete disks, NVRAM or state.
rig_require_stopped() {
	if [ -S "$RIG_SOCKET" ]; then
		rig_die "refusing to continue: socket $RIG_SOCKET is still present"
	fi
	local pid=
	if [ -f "$RIG_PID_FILE" ]; then
		pid=$(cat "$RIG_PID_FILE" 2>/dev/null || true)
	fi
	if [ -n "$pid" ] && rig_pid_alive "$pid"; then
		rig_die "refusing to continue: pid $pid in $RIG_PID_FILE is still alive"
	fi
}

# --fresh's reset: stop the guest (under the same lock; stop-rig.sh re-enters),
# then require proof it is gone before touching the disk, NVRAM or markers.
# If quiescence cannot be verified, this dies and deletes nothing. Callers
# hold the guest lock; RIG_STOP_SCRIPT exists so tests can supply a mock.
rig_fresh_reset() {
	local stop=${RIG_STOP_SCRIPT:-$RIG_REPO_ROOT/scripts/rig/stop-rig.sh}
	rig_log "--fresh: stopping the guest before removing state and disks"
	"$stop" >&2 || true
	rig_require_stopped
	rig_log "--fresh: removing state markers, disk $RIG_DISK and NVRAM"
	rm -rf "$RIG_STATE_DIR"
	rm -f "$RIG_DISK" "$RIG_NVRAM"
}

# The shared guest lock (issues #5, #7 and #24). iris-ci, the serial driver
# and the lifecycle scripts are single-session tools, so a client that starts,
# stops, provisions, installs an oracle, ships or runs takes this lock for the
# whole transaction rather than per call. One path and timeout for every
# client. Lifecycle scripts (start/stop/provision/oracle) hold it coarsely;
# smoke.sh and hinv-*.sh hold it per guest transaction. Composition is
# documented in docs/rig.md, "Lifecycle and locking".
RIG_GUEST_LOCK=${RIG_GUEST_LOCK:-$RIG_DIR/guest.lock}
RIG_GUEST_LOCK_TIMEOUT=${RIG_GUEST_LOCK_TIMEOUT:-3600}
export RIG_GUEST_LOCK RIG_GUEST_LOCK_TIMEOUT

# rig_with_guest_lock COMMAND...: run a whole guest transaction under the lock.
# Re-entrant through the exported RIG_GUEST_LOCK_HELD marker: a nested call
# (provision starting or stopping the emulator, oracle starting the guest) runs
# its command directly instead of flocking the same file again and deadlocking.
# The lock is an flock(2) on fd 9 of the one lock file; a timeout dies with a
# message rather than waiting without bound.
rig_with_guest_lock() {
	if [ -n "${RIG_GUEST_LOCK_HELD:-}" ]; then
		"$@"
		return $?
	fi

	exec 9>"$RIG_GUEST_LOCK"
	if ! flock -w "$RIG_GUEST_LOCK_TIMEOUT" 9; then
		exec 9>&-
		rig_die "timed out after ${RIG_GUEST_LOCK_TIMEOUT}s waiting for the guest lock at $RIG_GUEST_LOCK"
	fi
	export RIG_GUEST_LOCK_HELD=1

	local rc=0
	"$@" || rc=$?

	unset RIG_GUEST_LOCK_HELD
	flock -u 9 2>/dev/null || true
	exec 9>&-
	return "$rc"
}
