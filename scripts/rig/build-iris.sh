#!/usr/bin/env bash
#
# Build the rig's own IRIS emulator (issue #2).
#
# Clones techomancer/iris into the rig tree at a pinned commit and builds the
# release binary with the `lightning` feature (pedal-to-the-metal interpreter:
# no breakpoints or traceback updates, opcode fusion on). That is the build the
# guest install and later smoke tests run under.
#
# The Rust nightly comes from the rig-local RUSTUP_HOME (see lib.sh); the host
# C toolchain and the libraries the Rust dependencies probe for come from the
# flake's `rig` devshell, so the host side is pinned too.
#
# Usage: scripts/rig/build-iris.sh [--force]
#
#   --force    rebuild even when the binary is current
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
	cat <<'EOF'
Usage: scripts/rig/build-iris.sh [--force]

  --force      rebuild even when the binary is current
  -h, --help   show this help
EOF
}

force=
while [ $# -gt 0 ]; do
	case "$1" in
		--force) force=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) rig_die "unknown option: $1 (try --help)" ;;
	esac
done

mkdir -p "$RIG_LOG_DIR" "$RIG_RUSTUP_HOME" "$RIG_CARGO_HOME"

# Seed the rig-local Rust nightly on first use. The nix rig shell carries
# rustup, so this needs no system Rust.
if ! compgen -G "$RIG_RUSTUP_HOME/toolchains/*/bin/cargo" >/dev/null; then
	rig_log "installing Rust nightly into $RIG_RUSTUP_HOME"
	RUSTUP_HOME="$RIG_RUSTUP_HOME" CARGO_HOME="$RIG_CARGO_HOME" \
		nix develop "$RIG_REPO_ROOT#rig" --command \
		rustup toolchain install nightly --profile minimal
fi

if [ -d "$RIG_IRIS_DIR/.git" ]; then
	rig_log "updating $RIG_IRIS_DIR"
	git -C "$RIG_IRIS_DIR" fetch --quiet --tags origin
else
	rig_log "cloning $RIG_IRIS_REPO"
	git clone --quiet "$RIG_IRIS_REPO" "$RIG_IRIS_DIR"
fi

git -C "$RIG_IRIS_DIR" checkout --quiet --force "$RIG_IRIS_COMMIT"
rig_log "iris at $(git -C "$RIG_IRIS_DIR" rev-parse --short HEAD)"

if [ -z "$force" ] && [ -x "$RIG_IRIS" ] && [ -x "$RIG_IRIS_CI" ]; then
	rig_log "binaries already built (use --force to rebuild)"
	exit 0
fi

rust_bin_dir=$(echo "$RIG_RUSTUP_HOME"/toolchains/*/bin)
[ -x "$rust_bin_dir/cargo" ] || rig_die "no cargo in $rust_bin_dir"

rig_log "building iris (release, lightning)"
(
	cd "$RIG_IRIS_DIR"
	# Pin the installed nightly: a bare `channel = "nightly"` makes rustup
	# re-check for a new nightly on every cargo invocation.
	RUSTUP_TOOLCHAIN=nightly-x86_64-unknown-linux-gnu \
		RUSTUP_HOME="$RIG_RUSTUP_HOME" CARGO_HOME="$RIG_CARGO_HOME" \
		nix develop "$RIG_REPO_ROOT#rig" --command \
		cargo build --release --features lightning
) 2>&1 | tee "$RIG_LOG_DIR/build-iris.log"

[ -x "$RIG_IRIS" ] || rig_die "build finished without producing $RIG_IRIS"
[ -x "$RIG_IRIS_CI" ] || rig_die "build finished without producing $RIG_IRIS_CI"
rig_log "built $("$RIG_IRIS" --help 2>&1 | head -1)"
