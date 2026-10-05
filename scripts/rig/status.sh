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

# Markers are generation-bound (rig-state.py): "stale" means the disk or
# config changed since the phase ran, so the phase no longer counts as done.
for phase in phase-a label install verify; do
	echo "phase $phase: $(rig_state status "$phase")"
done

while IFS=$'\t' read -r slug _ _; do
	echo "oracle-$slug: $(rig_state status "oracle-$slug")"
done < <(python3 "$RIG_REPO_ROOT/scripts/rig/oracle-driver.py" sets)

if [ -L "$RIG_ORACLE_DIR/sysroot" ]; then
	echo "sysroot: -> $(readlink "$RIG_ORACLE_DIR/sysroot")"
elif [ -d "$RIG_ORACLE_DIR/sysroot" ]; then
	echo "sysroot: legacy directory at $RIG_ORACLE_DIR/sysroot"
fi

if [ -d "$RIG_ORACLE_DIR" ]; then
	echo "oracle: $RIG_ORACLE_DIR"
fi

if [ -f "$RIG_DISK" ]; then
	echo "disk: $(du -h "$RIG_DISK" | cut -f1) on disk"
else
	echo "disk: absent"
fi

if [ -f "$RIG_LOG_DIR/evidence.txt" ]; then
	echo "evidence: $RIG_LOG_DIR/evidence.txt"
	cat "$RIG_LOG_DIR/evidence.txt"
fi
