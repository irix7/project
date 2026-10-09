#!/usr/bin/env bash
#
# Build the rig's own IRIS emulator (issue #2).
#
# Clones the emulator fork (RIG_IRIS_REPO, irix7/iris) into the rig tree at the
# pinned commit and builds the release binary with the `lightning` feature
# (pedal-to-the-metal interpreter: no breakpoints or traceback updates, opcode
# fusion on). That is the build the guest install and later smoke tests run
# under.
#
# The Rust nightly comes from the rig-local RUSTUP_HOME (see lib.sh); the host
# C toolchain and the libraries the Rust dependencies probe for come from the
# flake's `rig` devshell, so the host side is pinned too.
#
# Resumption is receipted (issue #26): the state directory records the repo,
# the requested and resolved commits, the lightning feature, the rig-local
# rustc/cargo identity and the produced binary paths. Binaries are reused only
# while every one of those still matches; a changed pin, feature or nightly
# rebuilds. A dirty checkout is refused before any forced checkout unless
# --force-checkout is given, so local emulator changes are never discarded
# implicitly. `--force` means rebuild, not discard.
#
# Usage: scripts/rig/build-iris.sh [--force] [--force-checkout]
#
#   --force             rebuild even when the receipted binaries are current
#   --force-checkout    allow the forced checkout to discard tracked changes
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
	cat <<EOF
Usage: scripts/rig/build-iris.sh [--force] [--force-checkout]

  --force             rebuild even when the receipted binaries are current
  --force-checkout    allow the forced checkout to discard tracked changes
  -h, --help          show this help

The receipt at \$IRIX_RIG_DIR/state/build-iris.receipt records the repo,
requested and resolved commits, the lightning feature and the rig-local Rust
identity. Binaries are reused only while all of those match; --force rebuilds
anyway. A dirty checkout is refused unless --force-checkout is given.
EOF
}

force=
force_checkout=
while [ $# -gt 0 ]; do
	case "$1" in
		--force) force=1; shift ;;
		--force-checkout) force_checkout=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) rig_die "unknown option: $1 (try --help)" ;;
	esac
done

FEATURES=lightning
RECEIPT="$RIG_STATE_DIR/build-iris.receipt"

mkdir -p "$RIG_LOG_DIR" "$RIG_RUSTUP_HOME" "$RIG_CARGO_HOME" "$RIG_STATE_DIR"

# Seed the rig-local Rust nightly on first use. The nix rig shell carries
# rustup, so this needs no system Rust.
if ! compgen -G "$RIG_RUSTUP_HOME/toolchains/*/bin/cargo" >/dev/null; then
	rig_log "installing Rust nightly into $RIG_RUSTUP_HOME"
	RUSTUP_HOME="$RIG_RUSTUP_HOME" CARGO_HOME="$RIG_CARGO_HOME" \
		nix develop "$RIG_REPO_ROOT#rig" --command \
		rustup toolchain install nightly --profile minimal
fi

rust_bin_dir=
for d in "$RIG_RUSTUP_HOME"/toolchains/*/bin; do
	[ -x "$d/cargo" ] || continue
	rust_bin_dir=$d
	break
done
[ -n "$rust_bin_dir" ] || rig_die "no cargo under $RIG_RUSTUP_HOME/toolchains"

rust_version() {
	RUSTUP_HOME="$RIG_RUSTUP_HOME" CARGO_HOME="$RIG_CARGO_HOME" "$rust_bin_dir/$1" -V
}
rustc_identity=$(rust_version rustc)
cargo_identity=$(rust_version cargo)

resolved=
if [ -d "$RIG_IRIS_DIR/.git" ]; then
	resolved=$(git -C "$RIG_IRIS_DIR" rev-parse HEAD 2>/dev/null || true)
fi

receipt_field() {
	sed -n "s/^$1=//p" "$RECEIPT" 2>/dev/null | head -n 1
}

# receipt_matches: every requested and recorded input still matches, and the
# binary paths the receipt names are executable.
receipt_matches() {
	[ -f "$RECEIPT" ] || return 1
	[ "$(receipt_field repo)" = "$RIG_IRIS_REPO" ] || return 1
	[ "$(receipt_field requested_commit)" = "$RIG_IRIS_COMMIT" ] || return 1
	[ "$(receipt_field resolved_commit)" = "$resolved" ] || return 1
	[ "$(receipt_field features)" = "$FEATURES" ] || return 1
	[ "$(receipt_field rustc)" = "$rustc_identity" ] || return 1
	[ "$(receipt_field cargo)" = "$cargo_identity" ] || return 1
	[ "$(receipt_field iris)" = "$RIG_IRIS" ] || return 1
	[ "$(receipt_field iris_ci)" = "$RIG_IRIS_CI" ] || return 1
	[ -x "$RIG_IRIS" ] && [ -x "$RIG_IRIS_CI" ]
}

# Fast path: a matching receipt needs no network and no checkout.
if [ -z "$force" ] && receipt_matches; then
	rig_log "iris $(git -C "$RIG_IRIS_DIR" rev-parse --short HEAD 2>/dev/null || echo '?') already built (receipt matches; use --force to rebuild)"
	exit 0
fi

if [ -d "$RIG_IRIS_DIR/.git" ]; then
	if [ -n "$(git -C "$RIG_IRIS_DIR" status --porcelain)" ]; then
		if [ -z "$force_checkout" ]; then
			rig_die "$RIG_IRIS_DIR has local changes; commit or stash them, or pass --force-checkout to discard them"
		fi
		rig_log "discarding local changes in $RIG_IRIS_DIR (--force-checkout)"
	fi
	rig_log "updating $RIG_IRIS_DIR"
	git -C "$RIG_IRIS_DIR" remote set-url origin "$RIG_IRIS_REPO"
	git -C "$RIG_IRIS_DIR" fetch --quiet --tags origin
else
	rig_log "cloning $RIG_IRIS_REPO"
	git clone --quiet "$RIG_IRIS_REPO" "$RIG_IRIS_DIR"
fi

git -C "$RIG_IRIS_DIR" checkout --quiet --force "$RIG_IRIS_COMMIT"
resolved=$(git -C "$RIG_IRIS_DIR" rev-parse HEAD)
rig_log "iris at $(git -C "$RIG_IRIS_DIR" rev-parse --short HEAD)"

# The checked-out commit may now match an existing receipt (for example, the
# tree had drifted); reuse it rather than rebuilding.
if [ -z "$force" ] && receipt_matches; then
	rig_log "binaries already built for $(git -C "$RIG_IRIS_DIR" rev-parse --short HEAD)"
	exit 0
fi

rig_log "building iris (release, ${FEATURES})"
(
	cd "$RIG_IRIS_DIR"
	# Pin the installed nightly: a bare `channel = "nightly"` makes rustup
	# re-check for a new nightly on every cargo invocation.
	RUSTUP_TOOLCHAIN=nightly-x86_64-unknown-linux-gnu \
		RUSTUP_HOME="$RIG_RUSTUP_HOME" CARGO_HOME="$RIG_CARGO_HOME" \
		nix develop "$RIG_REPO_ROOT#rig" --command \
		cargo build --release --features "$FEATURES"
) 2>&1 | tee "$RIG_LOG_DIR/build-iris.log"

[ -x "$RIG_IRIS" ] || rig_die "build finished without producing $RIG_IRIS"
[ -x "$RIG_IRIS_CI" ] || rig_die "build finished without producing $RIG_IRIS_CI"

receipt_tmp="${RECEIPT}.tmp.$$"
{
	printf 'repo=%s\n' "$RIG_IRIS_REPO"
	printf 'requested_commit=%s\n' "$RIG_IRIS_COMMIT"
	printf 'resolved_commit=%s\n' "$resolved"
	printf 'features=%s\n' "$FEATURES"
	printf 'rustc=%s\n' "$rustc_identity"
	printf 'cargo=%s\n' "$cargo_identity"
	printf 'iris=%s\n' "$RIG_IRIS"
	printf 'iris_ci=%s\n' "$RIG_IRIS_CI"
} > "$receipt_tmp"
mv "$receipt_tmp" "$RECEIPT"

rig_log "built $("$RIG_IRIS" --help 2>&1 | head -1)"
