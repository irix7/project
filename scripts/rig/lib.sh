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

# Upstream emulator and the commit this rig is pinned to.
RIG_IRIS_REPO=${RIG_IRIS_REPO:-https://github.com/techomancer/iris}
RIG_IRIS_COMMIT=${RIG_IRIS_COMMIT:-320c38aa44013336a53cbb828674702e552afdd9}

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

# A compiled iris is the precondition of everything else.
rig_require_iris() {
	[ -x "$RIG_IRIS" ] || rig_die "no iris binary at $RIG_IRIS (run scripts/rig/build-iris.sh)"
	[ -x "$RIG_IRIS_CI" ] || rig_die "no iris-ci binary at $RIG_IRIS_CI"
}

# All iris-ci calls address our socket, never the default /tmp/iris.sock.
ic() {
	IRIS_SOCKET="$RIG_SOCKET" "$RIG_IRIS_CI" "$@"
}

# True when the rig's CI socket answers.
rig_running() {
	[ -S "$RIG_SOCKET" ] && ic ping >/dev/null 2>&1
}
