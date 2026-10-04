#!/usr/bin/env bash
#
# Show the rig's state: emulator liveness, socket, disks, media, last evidence.
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
	cat <<'EOF'
Usage: scripts/rig/status.sh

Print emulator liveness, phase markers, disk size and the last evidence.
EOF
}

case "${1:-}" in
	-h|--help) usage; exit 0 ;;
	"") ;;
	*) rig_die "unknown option: $1 (try --help)" ;;
esac

if rig_running; then
	echo "emulator: running ($RIG_SOCKET)"
else
	echo "emulator: stopped ($RIG_SOCKET)"
fi

for phase in phase-a label install verify; do
	if [ -f "$RIG_STATE_DIR/$phase.done" ]; then
		echo "phase $phase: done $(cat "$RIG_STATE_DIR/$phase.done")"
	else
		echo "phase $phase: pending"
	fi
done

if [ -f "$RIG_DISK" ]; then
	echo "disk: $(du -h "$RIG_DISK" | cut -f1) on disk"
else
	echo "disk: absent"
fi

if [ -f "$RIG_LOG_DIR/evidence.txt" ]; then
	echo "evidence: $RIG_LOG_DIR/evidence.txt"
	cat "$RIG_LOG_DIR/evidence.txt"
fi
