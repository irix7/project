#!/usr/bin/env bash
#
# Recursive-mutex and pthread helper probe for the mips-sgi-irix6.5 cross
# (issue #31).
#
# The cross builds scripts/smoke/gthread-recursive.c for o32 and n32, links
# it against the capture's libpthread.so, ships both binaries to the guest in
# one fail-closed transaction (scripts/lib/guest-txn.sh) and diffs their
# stdout against scripts/smoke/gthread-recursive.expected. The probe checks
# the contract libgcc's gthr-posix selection relies on: a recursive mutex
# locks twice on one thread and unlocks twice without deadlock, a second
# thread's try-lock fails with EBUSY while it is held and succeeds after
# release, initialisation and invalid-type errors behave, pthread_equal
# compares ids (and the entry point exists), sched_yield returns zero, and
# the rwlock contract works. A missing symbol fails at link, not silently at
# a weak reference.
#
# The probe is independently authored; no SGI or captured material is
# committed (ADR-0001). The guest work runs under the rig's shared guest
# lock, in a per-run /tmp directory, and each iris-ci call is bounded by
# --timeout.
#
# An unavailable prefix, cross, sysroot, rig, guest login or capture
# libpthread.so is a clear skip, never a pass. A link failure once the
# capture has libpthread.so, a transport failure, a non-zero guest status
# or any stdout difference fails closed.
#
# Usage: scripts/test-gthread-recursive.sh [--prefix DIR] [--sysroot DIR]
#                                           [--timeout SEC]
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

PROBE="$REPO_ROOT/scripts/smoke/gthread-recursive.c"
EXPECTED="$REPO_ROOT/scripts/smoke/gthread-recursive.expected"

PREFIX=${IRIX_PREFIX:-${REPO_ROOT}/.scratch/toolchain-16.2.0/prefix}
SYSROOT=
SYSROOT_EXPLICIT=0
TIMEOUT=300

usage() {
	cat <<'EOF'
Usage: scripts/test-gthread-recursive.sh [--prefix DIR] [--sysroot DIR]
                                          [--timeout SEC]

Build scripts/smoke/gthread-recursive.c with the cross for o32 and n32,
link it against the capture's libpthread.so, run both binaries on the guest
under one fail-closed transaction and diff their stdout against
scripts/smoke/gthread-recursive.expected.

  --prefix DIR     cross installation prefix
                   (default $IRIX_PREFIX or $PWD/.scratch/toolchain-16.2.0/prefix)
  --sysroot DIR    capture to link against (default: gcc -print-sysroot)
  --timeout SEC    timeout for each iris-ci call (default 300)
  -h, --help       show this help

The probe needs usr/lib/libpthread.so and usr/lib32/libpthread.so in the
capture; both are listed in scripts/rig/sysroot.files, so recapture the
sysroot after adding them. Missing inputs are a clear skip, never a pass;
a symbol that fails to link, a transport failure, a non-zero guest status
or any stdout difference fails.
EOF
}

die() {
	echo "FAIL: $*" >&2
	exit 1
}

skip() {
	echo "skip: $*"
	exit 0
}

while [ $# -gt 0 ]; do
	case "$1" in
		--prefix) PREFIX=$2; shift 2 ;;
		--sysroot) SYSROOT=$2; SYSROOT_EXPLICIT=1; shift 2 ;;
		--timeout) TIMEOUT=$2; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

[ -f "$PROBE" ] || die "probe source not found: $PROBE"
[ -f "$EXPECTED" ] || die "expected output not found: $EXPECTED"
[ -d "$PREFIX" ] ||
	skip "toolchain prefix not found: $PREFIX (build it with scripts/build-toolchain.sh, or pass --prefix)"
PREFIX=$(cd "$PREFIX" && pwd)

CC="$PREFIX/bin/${TARGET}-gcc"
READELF="$PREFIX/bin/${TARGET}-readelf"
[ -x "$CC" ] || skip "cross compiler not found: $CC (toolchain not built?)"
[ -x "$READELF" ] || skip "cross readelf not found: $READELF"

if [ "$SYSROOT_EXPLICIT" -eq 1 ]; then
	[ -d "$SYSROOT" ] || die "sysroot not found: $SYSROOT"
	SYSROOT=$(cd "$SYSROOT" && pwd)
else
	SYSROOT=$("$CC" -print-sysroot)
	[ -n "$SYSROOT" ] ||
		skip "cross has no sysroot configured ($CC -print-sysroot is empty); rebuild with scripts/build-toolchain.sh --sysroot DIR"
fi
[ -d "$SYSROOT" ] || die "sysroot does not exist: $SYSROOT"
sysroot_arg=("--sysroot=$SYSROOT")

# The link is the point of the probe: the capture must ship the pthread
# entry points. Until the recapture lists it, this is unavailable input
# rather than a failure of the toolchain.
for lib in usr/lib/libpthread.so usr/lib32/libpthread.so; do
	[ -e "$SYSROOT/$lib" ] ||
		skip "capture has no $lib; add it to scripts/rig/sysroot.files and recapture the sysroot"
done

# shellcheck source=scripts/rig/lib.sh
source "$REPO_ROOT/scripts/rig/lib.sh"

[ -x "$RIG_IRIS_CI" ] ||
	skip "rig control tool not found: $RIG_IRIS_CI (run scripts/rig/build-iris.sh)"
rig_running ||
	skip "rig is not running (no answer on $RIG_SOCKET); start it with scripts/rig/start-rig.sh"

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# --- host-side cross build -------------------------------------------------
#
# A missing pthread symbol is an explicit link failure here: the probe calls
# pthread_equal through a weak reference but every other entry point
# strongly, so a capture libpthread.so without the contract cannot pass.

for abi in o32 n32; do
	case "$abi" in
		o32) mabi=32 ;;
		n32) mabi=n32 ;;
	esac
	"$CC" "${sysroot_arg[@]}" -mabi="$mabi" -O2 \
		-o "$tmpdir/gthread-recursive.$abi" "$PROBE" -lpthread ||
		die "cross link failed for $abi against $SYSROOT; the capture's libpthread.so is missing a contract symbol"
	"$READELF" -l "$tmpdir/gthread-recursive.$abi" \
		>"$tmpdir/gthread-recursive.$abi.phdr" ||
		die "readelf failed on the $abi cross binary"
	grep -q 'INTERP' "$tmpdir/gthread-recursive.$abi.phdr" ||
		die "$abi cross binary is not dynamically linked (no PT_INTERP); ADR-0006 requires the dynamic model"
done

# --- guest runner ----------------------------------------------------------
#
# One shipped POSIX sh script writes stdout, stderr and the exit status for
# each ABI on every path, plus a transaction status, so the host requires
# every retrieval and cannot accept a partial run.

runner="$tmpdir/gthread-recursive-run.sh"
cat >"$runner" <<'RUNNER'
#!/bin/sh
#
# Guest-side recursive-mutex runner (issue #31). Shipped by
# scripts/test-gthread-recursive.sh; no SGI or captured material is part of
# it.
#
set -u

dir=$1
cd "$dir" || exit 90

for abi in o32 n32; do
	chmod +x "gthread-recursive.$abi" || exit 90
	"./gthread-recursive.$abi" > "gthread-recursive.$abi.stdout" \
		2> "gthread-recursive.$abi.stderr"
	echo $? > "gthread-recursive.$abi.status"
done

echo 0 > txn.status
exit 0
RUNNER

# --- guest transaction -----------------------------------------------------

guest_dir="/tmp/gthread-recursive-$$-$RANDOM"

login_status=0
ic -q run "echo gthread-recursive-ready" --timeout "$TIMEOUT" >/dev/null ||
	login_status=$?
[ "$login_status" -eq 0 ] ||
	skip "guest login did not answer on $RIG_SOCKET (the rig is up but the guest shell is not)"

gets=()
for abi in o32 n32; do
	for suffix in stdout stderr status; do
		gets+=(--get "$guest_dir/gthread-recursive.$abi.$suffix" \
			"$tmpdir/gthread-recursive.$abi.$suffix")
	done
done

txn_status=0
"$REPO_ROOT/scripts/lib/guest-txn.sh" \
	--timeout "$TIMEOUT" \
	--guest-dir "$guest_dir" \
	--label "gthread recursive" \
	--put "$tmpdir/gthread-recursive.o32" "$guest_dir/gthread-recursive.o32" \
	--put "$tmpdir/gthread-recursive.n32" "$guest_dir/gthread-recursive.n32" \
	--put "$runner" "$guest_dir/gthread-recursive-run.sh" \
	--run "rehash; sh $guest_dir/gthread-recursive-run.sh $guest_dir" \
	"${gets[@]}" \
	--status "$guest_dir/txn.status" "$tmpdir/txn.status" ||
	txn_status=$?

case "$txn_status" in
	0) ;;
	93) skip "rig stopped answering while the gthread transaction ran" ;;
	*) die "guest transaction did not complete (status $txn_status); no evidence accepted" ;;
esac

ic -q run "rm -rf $guest_dir" --timeout "$TIMEOUT" >/dev/null 2>&1 || true

# --- host-side classification ----------------------------------------------

failures=0
for abi in o32 n32; do
	status_file="$tmpdir/gthread-recursive.$abi.status"
	out_file="$tmpdir/gthread-recursive.$abi.stdout"
	err_file="$tmpdir/gthread-recursive.$abi.stderr"
	[ -f "$status_file" ] && [ -f "$out_file" ] ||
		die "$abi: output or status was not retrieved (transport failure)"
	rc=$(cat "$status_file")
	case "$rc" in
		'' | *[!0-9]*) die "$abi: unreadable guest exit status: '$rc'" ;;
	esac
	if [ "$rc" -ne 0 ]; then
		echo "FAIL: $abi probe exited $rc, expected 0" >&2
		if [ -s "$out_file" ]; then
			sed 's/^/  /' "$out_file" >&2
		fi
		if [ -s "$err_file" ]; then
			sed 's/^/  /' "$err_file" >&2
		fi
		failures=$((failures + 1))
		continue
	fi
	if ! diff -u "$EXPECTED" "$out_file" >"$tmpdir/$abi.diff" 2>&1; then
		echo "FAIL: $abi stdout does not match $EXPECTED:" >&2
		sed 's/^/  /' "$tmpdir/$abi.diff" >&2
		failures=$((failures + 1))
		continue
	fi
	if [ -s "$err_file" ]; then
		echo "note: $abi probe wrote to stderr:" >&2
		sed 's/^/  /' "$err_file" >&2
	fi
done

if [ "$failures" -ne 0 ]; then
	echo "test-gthread-recursive: FAIL (${failures} check(s))" >&2
	exit 1
fi

echo "test-gthread-recursive: pass - o32 and n32 recursive mutex, helpers and rwlock contracts hold"
