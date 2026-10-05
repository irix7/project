#!/usr/bin/env bash
#
# Build the MIPSpro oracle and capture the guest sysroot (issue #3).
#
# The fresh 6.5.7m guest becomes the project's oracle in three steps:
#
#   1. install  MIPSpro 7.3 (All-Compiler CD), its headers/startfiles (IRIX 6.5
#               Development Libraries) and the 7.4 runtime (CEE) with inst,
#               driven over the serial console by oracle-driver.py
#   2. patch    make MIPSpro runnable without an SGI licence: either install a
#               locally prepared driver or a licence file. Neither is ever
#               committed (ADR-0001); both are taken from the rig tree.
#   3. capture  compile oracle/hello.c with cc natively, record the output and
#               objects, and pull the headers, crt startfiles and libc/libm
#               out of the guest as sysroot.tar.gz using iris-ci put/get.
#
# The captured sysroot, objects and logs contain SGI material and stay under
# $RIG_ORACLE_DIR on shared storage; only the scripts live in the repo.
#
# Usage: scripts/rig/oracle.sh [install|patch|capture|all|status] [--force]
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ORACLE_FILES="$RIG_REPO_ROOT/scripts/rig/sysroot.files"
ORACLE_DRIVER="$RIG_REPO_ROOT/scripts/rig/oracle-driver.py"
ORACLE_CAPTURE="$RIG_REPO_ROOT/scripts/rig/oracle-capture.py"

usage() {
	cat <<'EOF'
Usage: scripts/rig/oracle.sh <command> [--force]

  install   inst MIPSpro 7.3 + CEE 7.4 + dev headers from the oracle media
  patch     install a local MIPSpro driver or licence so cc runs (never published)
  capture   build oracle/hello.c in the guest and pull out objects + sysroot
  all       install, patch, then capture
  status    show install markers and the captured oracle tree

Options:
  --force   redo install steps even when their markers exist

Media: the three oracle ISOs must be in $RIG_MEDIA_DIR. Set
IRIX_ORACLE_MEDIA_SOURCE to a directory to copy missing ones from.

Licence: cc needs an SGI licence. Set IRIX_MIPSPRO_DRIVER to a locally
patched 7.3 driver (default $RIG_ORACLE_DIR/mipspro-7.3-n32-driver) or
IRIX_MIPSPRO_LICENSE to a valid /var/flexlm/license.dat. Neither is
shipped with this repo.
EOF
}

# The media and products are spelled once, in oracle-driver.py's ORACLE_SETS;
# `sets` prints them as slug<TAB>media<TAB>products for the shell to walk.
oracle_sets() {
	python3 "$ORACLE_DRIVER" sets
}

rig_require_oracle_media() {
	local missing=() slug disc products
	while IFS=$'\t' read -r slug disc products; do
		[ -f "$RIG_MEDIA_DIR/$disc" ] || missing+=("$disc")
	done < <(oracle_sets)
	[ "${#missing[@]}" -eq 0 ] && return 0

	if [ -n "${IRIX_ORACLE_MEDIA_SOURCE:-}" ]; then
		mkdir -p "$RIG_MEDIA_DIR"
		local still_missing=()
		for disc in "${missing[@]}"; do
			if [ -f "$IRIX_ORACLE_MEDIA_SOURCE/$disc" ]; then
				cp -n "$IRIX_ORACLE_MEDIA_SOURCE/$disc" "$RIG_MEDIA_DIR/"
			else
				still_missing+=("$disc")
			fi
		done
		missing=("${still_missing[@]}")
	fi

	if [ "${#missing[@]}" -gt 0 ]; then
		printf 'rig: oracle media missing from %s:\n' "$RIG_MEDIA_DIR" >&2
		printf '  %s\n' "${missing[@]}" >&2
		rig_die "copy the oracle ISOs there or set IRIX_ORACLE_MEDIA_SOURCE"
	fi
}

rig_ensure_running() {
	rig_require_iris
	rig_running || "$RIG_REPO_ROOT/scripts/rig/start-rig.sh"
}

rig_ensure_shell() {
	python3 "$ORACLE_DRIVER" \
		--socket "$RIG_SOCKET" --log "$RIG_DRIVER_LOG" --ic "$RIG_IRIS_CI" \
		ensure-shell
}

rig_mount_disc() {
	local disc=$1
	ic run "umount /CDROM" --timeout 120 >/dev/null 2>&1 || true
	ic cdrom-load 4 "$RIG_MEDIA_DIR/$disc"
	ic run "mount -t efs -o ro /dev/dsk/dks0d4s7 /CDROM" --timeout 300
}

cmd_install() {
	local force=$1
	rig_require_oracle_media
	rig_ensure_running
	rig_ensure_shell

	if [ -n "$force" ]; then
		rm -f "$RIG_STATE_DIR"/oracle-*.done
	fi
	mkdir -p "$RIG_STATE_DIR"

	local slug disc products out
	while IFS=$'\t' read -r slug disc products; do
		if rig_state_marked "oracle-$slug"; then
			rig_log "install $slug: already done"
			continue
		fi
		rig_log "install $slug from $disc"
		rig_mount_disc "$disc"
		python3 "$ORACLE_DRIVER" \
			--socket "$RIG_SOCKET" --log "$RIG_DRIVER_LOG" \
			inst "$products"

		# A set is marked only after the guest's own versions listing names
		# every product that was requested, not merely after inst returned 0.
		if ! out=$(ic run "versions $products | cat" --timeout 300); then
			rig_die "install $slug: versions $products could not be read"
		fi
		if ! printf '%s\n' "$out" | python3 "$ORACLE_DRIVER" check-products "$products"; then
			rig_die "install $slug: the guest does not name the requested products"
		fi
		rig_state mark "oracle-$slug" \
			--postcondition "inst installed and versions named: $products"
	done < <(oracle_sets)

	ic run "umount /CDROM" --timeout 120 >/dev/null 2>&1 || true
	rig_log "oracle install complete"
}

cmd_patch() {
	rig_ensure_running
	rig_ensure_shell

	local patched=${IRIX_MIPSPRO_DRIVER:-$RIG_ORACLE_DIR/mipspro-7.3-n32-driver}
	ic put "$RIG_REPO_ROOT/oracle/hello.c" --to /tmp/hello.c --timeout 300
	if [ -f "$patched" ]; then
		rig_log "installing local MIPSpro 7.3 driver from $patched (never published)"
		ic put "$patched" --to /tmp/mipspro-driver --timeout 600
		ic run "cp /tmp/mipspro-driver /usr/lib32/cmplrs/driver" --timeout 300
	elif [ -n "${IRIX_MIPSPRO_LICENSE:-}" ]; then
		rig_log "installing MIPSpro licence from $IRIX_MIPSPRO_LICENSE"
		ic put "$IRIX_MIPSPRO_LICENSE" --to /var/flexlm/license.dat --timeout 300
	else
		rig_log "no local driver or licence; relying on the guest's current state"
	fi

	# A compile is the only honest licence probe: `cc -version` succeeds even
	# when the phases refuse to run.
	if ! ic run "rehash; cd /tmp; cc -c hello.c -o hello.probe.o" --timeout 600; then
		rig_die "MIPSpro cannot compile: set IRIX_MIPSPRO_DRIVER to a local 7.3 driver or IRIX_MIPSPRO_LICENSE to a licence file"
	fi
	ic run "rm -f /tmp/hello.probe.o" --timeout 120 >/dev/null 2>&1 || true
	rig_log "cc runs; oracle is licensed or locally patched"
}

# A retrieval that produced no file is a failed transfer, never an empty
# capture member: validate it at the boundary, then again before publication.
oracle_get() {
	local src=$1 dest=$2
	ic get "$src" --to "$dest" --timeout 900
	[ -s "$dest" ] || rig_die "retrieval produced no file: $dest"
}

cmd_capture() {
	rig_ensure_running
	rig_ensure_shell
	mkdir -p "$RIG_ORACLE_DIR/generations"

	# A capture is a generation: bound to the guest's disk/config generation
	# and a UTC stamp, and built under a hidden directory. Nothing a host
	# build reads is touched until the whole generation has been validated
	# and published by a symlink swap.
	local gen stamp name cap_dir
	gen=$(rig_state generation)
	stamp=$(date -u +%Y%m%dT%H%M%SZ)
	name=$(python3 "$ORACLE_CAPTURE" capture-name --generation "$gen" --stamp "$stamp")
	cap_dir="$RIG_ORACLE_DIR/generations/.build-$name"
	rm -rf "$cap_dir"
	mkdir -p "$cap_dir"
	rig_log "capture generation $name (building under ${cap_dir##*/})"

	# One fresh build directory per ABI. MIPSpro's -S ignores -o (it always
	# writes <basename>.s), and a later link in the same directory deletes
	# stale <basename>.o files, so the two ABIs must not share a directory.
	ic run "rm -rf /tmp/oracle && mkdir /tmp/oracle && mkdir /tmp/oracle/o32 /tmp/oracle/n32" --timeout 120
	ic put "$RIG_REPO_ROOT/oracle/hello.c" --to /tmp/oracle/o32/hello.c --timeout 300
	ic put "$RIG_REPO_ROOT/oracle/hello.c" --to /tmp/oracle/n32/hello.c --timeout 300

	rig_log "recording compiler environment"
	{
		printf '# MIPSpro oracle environment\n# captured %s\n' "$(date -Iseconds)"
		printf '\n$ uname -a\n'
		ic run "uname -a" --timeout 120
		printf '\n$ cc -version\n'
		ic run "cc -version" --timeout 120
		printf '\n$ versions compiler_dev c_dev c_fe compiler_eoe irix_dev dev | cat\n'
		ic run "versions compiler_dev c_dev c_fe compiler_eoe irix_dev dev | cat" --timeout 300
	} >"$cap_dir/environment.txt"
	[ -s "$cap_dir/environment.txt" ] || rig_die "environment capture is empty"

	rig_log "building hello with cc (o32, then n32)"
	# Link before the -S/-c steps: the link removes stale same-basename
	# objects, which would take the freshly built hello.o32.o with it.
	ic run "cd /tmp/oracle/o32 && cc -v -o32 -o hello.o32 hello.c -lm >& hello.o32.build.log && ./hello.o32 > hello.o32.output" --timeout 900
	ic run "cd /tmp/oracle/o32 && cc -o32 -S hello.c && cc -o32 -c hello.c -o hello.o32.o" --timeout 900
	ic run "cd /tmp/oracle/n32 && cc -v -n32 -o hello.n32 hello.c -lm >& hello.n32.build.log && ./hello.n32 > hello.n32.output" --timeout 900
	ic run "cd /tmp/oracle/n32 && cc -n32 -S hello.c && cc -n32 -c hello.c -o hello.n32.o" --timeout 900

	local src dest
	while read -r src dest; do
		oracle_get "$src" "$cap_dir/$dest"
	done <<'EOF'
/tmp/oracle/o32/hello.o32 hello.o32
/tmp/oracle/o32/hello.s hello.o32.s
/tmp/oracle/o32/hello.o32.o hello.o32.o
/tmp/oracle/o32/hello.o32.build.log hello.o32.build.log
/tmp/oracle/o32/hello.o32.output hello.o32.output
/tmp/oracle/n32/hello.n32 hello.n32
/tmp/oracle/n32/hello.s hello.n32.s
/tmp/oracle/n32/hello.n32.o hello.n32.o
/tmp/oracle/n32/hello.n32.build.log hello.n32.build.log
/tmp/oracle/n32/hello.n32.output hello.n32.output
EOF

	rig_log "capturing the sysroot"
	ic put "$ORACLE_FILES" --to /tmp/sysroot.files --timeout 300
	# Single-quoted so the backticks reach the guest's csh, which word-splits
	# the manifest into tar's argument list.
	ic run 'cd / && tar cf /tmp/sysroot.tar `cat /tmp/sysroot.files`' --timeout 900
	ic run "gzip -f /tmp/sysroot.tar" --timeout 300
	oracle_get /tmp/sysroot.tar.gz "$cap_dir/sysroot.tar.gz"

	# Confined extraction, typed attestation (content, types, symlink chains),
	# manifest and full validation all happen in the build directory. Only a
	# capture that passes every check is published; a failure leaves the
	# previous generation and sysroot symlink exactly as they were.
	python3 "$ORACLE_CAPTURE" finalise "$cap_dir"
	python3 "$ORACLE_CAPTURE" publish "$RIG_ORACLE_DIR" "$cap_dir" "$name"
	rig_log "oracle capture published: $RIG_ORACLE_DIR/sysroot -> generations/$name/sysroot"
}

cmd_status() {
	local slug media products
	while IFS=$'\t' read -r slug media products; do
		printf 'oracle-%-8s %s\n' "$slug" "$(rig_state status "oracle-$slug")"
	done < <(oracle_sets)

	if [ -d "$RIG_ORACLE_DIR" ]; then
		echo
		if [ -L "$RIG_ORACLE_DIR/sysroot" ]; then
			echo "sysroot: -> $(readlink "$RIG_ORACLE_DIR/sysroot")"
		elif [ -d "$RIG_ORACLE_DIR/sysroot" ]; then
			echo "sysroot: legacy directory (not generation-managed)"
		fi
		if [ -d "$RIG_ORACLE_DIR/generations" ]; then
			echo "generations:"
			# shellcheck disable=SC2012
			ls -1 "$RIG_ORACLE_DIR/generations" | sed 's/^/  /'
		fi
		echo
		ls -la "$RIG_ORACLE_DIR"
	fi
}

cmd=${1:-}
if [ $# -gt 0 ]; then
	shift
fi
force=
while [ $# -gt 0 ]; do
	case "$1" in
		--force) force=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) rig_die "unknown option: $1 (try --help)" ;;
	esac
done

case "$cmd" in
	install|patch|capture|all|status) ;;
	""|-h|--help) usage; exit 0 ;;
	*) usage >&2; rig_die "unknown command: $cmd" ;;
esac

oracle_main() {
	case "$cmd" in
		install) cmd_install "$force" ;;
		patch) cmd_patch ;;
		capture) cmd_capture ;;
		all)
			cmd_install "$force"
			cmd_patch
			cmd_capture
			;;
		status) cmd_status ;;
	esac
}

# Oracle work is one lifecycle transaction under the shared guest lock, like
# provision: its ensure-running start-rig call re-enters the lock rather than
# deadlocking, and no smoke or stop can interleave with an install or capture.
rig_with_guest_lock oracle_main
