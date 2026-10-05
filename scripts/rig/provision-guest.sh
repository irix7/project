#!/usr/bin/env bash
#
# Bring up a fresh IRIX 6.5.7m guest on the independent rig (issue #2).
#
# Steps, each skipped when its marker under $RIG_STATE_DIR exists:
#
#   1. build the rig's own iris binary (scripts/rig/build-iris.sh)
#   2. write the rig config and an empty 20GB boot disk (scripts/rig/new-rig.sh)
#   3. headless NVRAM seed: SystemPartition, OSLoadPartition, console=d
#   4. label/create the disk with fx.ARCS from the install CD
#   5. install from the four 6.5.7 media discs and restart into IRIX
#   6. log in and record uname -a / hinv as evidence
#
# The install runs `go` for one to two emulated hours; run this detached and
# watch $RIG_LOG_DIR/serial.log. If it fails, fix the cause and rerun: finished
# steps are skipped, but a half-finished install needs a fresh disk
# (`--fresh`, which also removes the state markers).
#
# Usage: scripts/rig/provision-guest.sh [--fresh]
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
	cat <<'EOF'
Usage: scripts/rig/provision-guest.sh [--fresh]

  --fresh      remove the state markers, NVRAM and disk, then start over
  -h, --help   show this help
EOF
}

fresh=
while [ $# -gt 0 ]; do
	case "$1" in
		--fresh) fresh=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) rig_die "unknown option: $1 (try --help)" ;;
	esac
done

rig_run_driver() {
	IRIS_SOCKET="$RIG_SOCKET" \
		RIG_DRIVER_LOG="$RIG_DRIVER_LOG" \
		RIG_STATE_DIR="$RIG_STATE_DIR" \
		RIG_IRIS_CI="$RIG_IRIS_CI" \
		RIG_EVIDENCE="$RIG_LOG_DIR/evidence.txt" \
		python3 "$RIG_REPO_ROOT/scripts/rig/install-driver.py" "$@"
}

provision_guest() {
	if [ -n "$fresh" ]; then
		rig_fresh_reset
	fi

	"$RIG_REPO_ROOT/scripts/rig/build-iris.sh"
	"$RIG_REPO_ROOT/scripts/rig/new-rig.sh"

	if [ ! -f "$RIG_STATE_DIR/phase-a.done" ]; then
		rig_log "phase A: seeding NVRAM (headless)"
		"$RIG_REPO_ROOT/scripts/rig/stop-rig.sh" >/dev/null 2>&1 || true
		"$RIG_REPO_ROOT/scripts/rig/start-rig.sh" --headless
		rig_run_driver phase-a
		"$RIG_REPO_ROOT/scripts/rig/stop-rig.sh"
	fi

	# The post-install restart reboots inside the same iris process, so labelling,
	# the install and verification share one long-running session.
	if [ ! -f "$RIG_STATE_DIR/verify.done" ]; then
		rig_running || "$RIG_REPO_ROOT/scripts/rig/start-rig.sh"
		[ -f "$RIG_STATE_DIR/label.done" ] || rig_run_driver label
		[ -f "$RIG_STATE_DIR/install.done" ] || rig_run_driver install
		[ -f "$RIG_STATE_DIR/verify.done" ] || rig_run_driver verify
	fi

	rig_log "provisioning complete"
	rig_log "serial log:  $RIG_SERIAL_LOG"
	rig_log "driver log:  $RIG_DRIVER_LOG"
	rig_log "evidence:    $RIG_LOG_DIR/evidence.txt"
}

# The whole provision run is one lifecycle transaction; its internal
# start-rig/stop-rig calls re-enter through RIG_GUEST_LOCK_HELD.
rig_with_guest_lock provision_guest
