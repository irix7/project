#!/usr/bin/env bash
#
# stdint oracle comparison for the mips-sgi-irix6.5 cross (issue #30).
#
# The guest-free scripts/test-hosted-stdint.sh proves hosted <stdint.h> works
# and matches GCC's ABI builtins. This script is the guest half: it compiles
# oracle/stdint-probe.c with the cross for o32 and n32, runs the two
# binaries on the IRIX guest, compiles and runs the same probe there with the
# native MIPSpro cc (o32 and n32) and diffs the two stdout streams per ABI.
# The capture's headers are read only inside the guest; no SGI or captured
# material is committed (ADR-0001).
#
# The MIPSpro leg needs the licensed or locally patched guest documented in
# docs/oracle.md. Prefix, sysroot, rig socket, guest login and cc are each
# checked, and an unavailable input is a clear skip, never a pass; transport
# failures and any output difference fail closed. Every guest transaction
# runs under lib.sh's shared guest lock, and the guest work lives in a
# namespaced /tmp directory unique to the run.
#
# Host-side logic (probe shipping, output parsing, shape checks and the
# per-ABI diff) is unit-tested with a fake iris-ci in
# scripts/test-stdint-oracle.py.
#
# Usage: scripts/test-stdint-oracle.sh [--prefix DIR] [--sysroot DIR]
#                                      [--timeout SEC]
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

PROBE="$REPO_ROOT/oracle/stdint-probe.c"

PREFIX=${IRIX_PREFIX:-${REPO_ROOT}/.scratch/toolchain-16.2.0/prefix}
SYSROOT=
SYSROOT_EXPLICIT=0
TIMEOUT=600

usage() {
	cat <<'EOF'
Usage: scripts/test-stdint-oracle.sh [--prefix DIR] [--sysroot DIR]
                                     [--timeout SEC]

Compare the IRIX 6.5 cross's integer model with the guest's native MIPSpro
oracle: compile oracle/stdint-probe.c with the cross for o32 and n32, run
the binaries on the guest, compile and run the same probe with MIPSpro
cc -o32 and cc -n32 there, and diff stdout per ABI.

  --prefix DIR     cross installation prefix
                   (default $IRIX_PREFIX or $PWD/.scratch/toolchain-16.2.0/prefix)
  --sysroot DIR    capture to link against (default: gcc -print-sysroot)
  --timeout SEC    timeout for each iris-ci call (default 600)
  -h, --help       show this help

This is guest evidence, distinct from the guest-free
scripts/test-hosted-stdint.sh: it needs the running rig and a MIPSpro cc
that can compile (a locally patched driver or licence, docs/oracle.md).
An unavailable prefix, cross, sysroot, rig socket, guest login or cc is
reported as a clear skip and never as a pass; a transport failure or any
output difference fails.
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

# shellcheck source=scripts/rig/lib.sh
source "$REPO_ROOT/scripts/rig/lib.sh"

[ -x "$RIG_IRIS_CI" ] ||
	skip "rig control tool not found: $RIG_IRIS_CI (run scripts/rig/build-iris.sh)"
rig_running ||
	skip "rig is not running (no answer on $RIG_SOCKET); start it with scripts/rig/start-rig.sh"

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# --- host-side cross build -------------------------------------------------

for abi in o32 n32; do
	case "$abi" in
		o32) mabi=32 ;;
		n32) mabi=n32 ;;
	esac
	"$CC" "${sysroot_arg[@]}" -mabi="$mabi" \
		-o "$tmpdir/probe.gcc.$abi" "$PROBE" ||
		die "cross compile failed for $abi: see the diagnostics above"
	"$READELF" -l "$tmpdir/probe.gcc.$abi" >"$tmpdir/probe.gcc.$abi.phdr" ||
		die "readelf failed on the $abi cross binary"
	grep -q 'INTERP' "$tmpdir/probe.gcc.$abi.phdr" ||
		die "$abi cross binary is not dynamically linked (no PT_INTERP); ADR-0006 requires the dynamic model"
done

# --- guest runner ----------------------------------------------------------
#
# The guest-side work lives in one shipped POSIX sh script: it runs the two
# cross binaries and compiles and runs the native MIPSpro oracle, writing an
# output, stderr and status file per ABI (plus a compile flag when MIPSpro
# cannot compile). Explicit ./ paths keep it independent of csh's rehash,
# which the transaction runs anyway after the copies.

runner="$tmpdir/stdint-oracle-run.sh"
cat >"$runner" <<'RUNNER'
#!/bin/sh
#
# Guest-side stdint oracle runner (issue #30). Shipped by
# scripts/test-stdint-oracle.sh; no SGI or captured material is part of it.
#
# Every output file is written on every path, including the "unavailable"
# marker, so the host transaction can require every retrieval and cannot
# accept a partial run.
#
set -u

dir=$1
cd "$dir" || exit 90

for abi in o32 n32; do
	chmod +x "probe.gcc.$abi" || exit 90
	"./probe.gcc.$abi" > "probe.gcc.$abi.stdout" 2> "probe.gcc.$abi.stderr"
	echo $? > "probe.gcc.$abi.status"
done

for abi in o32 n32; do
	if cc -$abi -o "probe.cc.$abi" probe.c > "probe.cc.$abi.compile" 2>&1; then
		echo no > "probe.cc.$abi.unavailable"
		"./probe.cc.$abi" > "probe.cc.$abi.stdout" 2> "probe.cc.$abi.stderr"
		echo $? > "probe.cc.$abi.status"
	else
		echo yes > "probe.cc.$abi.unavailable"
		: > "probe.cc.$abi.stdout"
		: > "probe.cc.$abi.stderr"
		echo 127 > "probe.cc.$abi.status"
	fi
done

echo 0 > txn.status
exit 0
RUNNER

# --- guest transaction -----------------------------------------------------
#
# One fail-closed transaction through scripts/lib/guest-txn.sh under the
# shared guest lock: setup, ship, run the runner and pull every stream. The
# login probe is separate: a guest sitting at a login prompt is an available
# rig with an unavailable oracle, which is a skip, not a transport failure.

guest_dir="/tmp/stdint-oracle-$$-$RANDOM"

login_status=0
ic -q run "echo stdint-oracle-ready" --timeout "$TIMEOUT" >/dev/null ||
	login_status=$?
[ "$login_status" -eq 0 ] ||
	skip "guest login did not answer on $RIG_SOCKET (the rig is up but the guest shell is not)"

gets=()
for stem in probe.gcc.o32 probe.gcc.n32; do
	for suffix in stdout stderr status; do
		gets+=(--get "$guest_dir/$stem.$suffix" "$tmpdir/$stem.$suffix")
	done
done
for stem in probe.cc.o32 probe.cc.n32; do
	for suffix in stdout stderr status compile unavailable; do
		gets+=(--get "$guest_dir/$stem.$suffix" "$tmpdir/$stem.$suffix")
	done
done

txn_status=0
"$REPO_ROOT/scripts/lib/guest-txn.sh" \
	--timeout "$TIMEOUT" \
	--guest-dir "$guest_dir" \
	--label "stdint oracle" \
	--put "$PROBE" "$guest_dir/probe.c" \
	--put "$tmpdir/probe.gcc.o32" "$guest_dir/probe.gcc.o32" \
	--put "$tmpdir/probe.gcc.n32" "$guest_dir/probe.gcc.n32" \
	--put "$runner" "$guest_dir/stdint-oracle-run.sh" \
	--run "rehash; sh $guest_dir/stdint-oracle-run.sh $guest_dir" \
	"${gets[@]}" \
	--status "$guest_dir/txn.status" "$tmpdir/txn.status" ||
	txn_status=$?

case "$txn_status" in
	0) ;;
	93) skip "rig stopped answering while the stdint oracle transaction ran" ;;
	*) die "guest transaction did not complete (status $txn_status); no evidence accepted" ;;
esac

ic -q run "rm -rf $guest_dir" --timeout "$TIMEOUT" >/dev/null 2>&1 || true

# --- host-side classification ----------------------------------------------

# The expected o32/n32 integer model, independently authored from the C
# standard's exact-width, pointer-sized and greatest-width definitions. Both
# ABIs are ILP32, so one table serves both. This is the sanity check on the
# oracle itself (a broken or unexpected cc must not arbitrate silently); the
# cross output is held to it through the diff below. The shell and the host
# tests read the same file so the model exists once.
mapfile -t EXPECTED_LINES < <(grep -v '^[[:space:]]*$' "$REPO_ROOT/scripts/stdint-oracle.model")
[ "${#EXPECTED_LINES[@]}" -gt 0 ] ||
	die "integer model is empty: $REPO_ROOT/scripts/stdint-oracle.model"
PROBE_KEYS=("${EXPECTED_LINES[@]%%=*}")

# require_run LABEL STEM: the guest program ran and exited zero.
require_run() {
	local label=$1 stem=$2
	local status_file="$tmpdir/${stem}.status"
	local out_file="$tmpdir/${stem}.stdout"
	[ -f "$status_file" ] && [ -f "$out_file" ] ||
		die "$label: output or status was not retrieved (transport failure)"
	local rc
	rc=$(cat "$status_file")
	case "$rc" in
		'' | *[!0-9]*) die "$label: unreadable guest exit status: '$rc'" ;;
	esac
	[ "$rc" -eq 0 ] || die "$label exited $rc, expected 0"
	if [ -s "$tmpdir/${stem}.stderr" ]; then
		echo "note: $label wrote to stderr:" >&2
		sed 's/^/  /' "$tmpdir/${stem}.stderr" >&2
	fi
}

# validate_oracle FILE LABEL: the native MIPSpro output is exactly the
# expected IRIX model.
validate_oracle() {
	local file=$1 label=$2
	if ! diff -u <(printf '%s\n' "${EXPECTED_LINES[@]}") "$file" \
		>"$tmpdir/oracle-shape.diff" 2>&1; then
		echo "FAIL: $label output does not match the expected IRIX integer model:" >&2
		cat "$tmpdir/oracle-shape.diff" >&2
		return 1
	fi
	return 0
}

# validate_shape FILE LABEL: the cross output has exactly the expected keys
# in order, a value grammar and self-consistent pointer-sized types. It
# guards the diff against a truncated or degenerate capture without
# duplicating the oracle's value comparison, which is the diff's job.
validate_shape() {
	local file=$1 label=$2
	local -a got=()
	local i key value
	mapfile -t got <"$file"
	if [ "${#got[@]}" -ne "${#PROBE_KEYS[@]}" ]; then
		echo "FAIL: $label: expected ${#PROBE_KEYS[@]} result lines, got ${#got[@]}:" >&2
		sed 's/^/  /' "$file" >&2
		return 1
	fi
	for ((i = 0; i < ${#PROBE_KEYS[@]}; i++)); do
		key=${got[i]%%=*}
		if [ "$key" = "${got[i]}" ] || [ "$key" != "${PROBE_KEYS[i]}" ]; then
			printf 'FAIL: %s line %d: expected key %s, got %q\n' \
				"$label" "$((i + 1))" "${PROBE_KEYS[i]}" "${got[i]}" >&2
			return 1
		fi
	done

	local -A values=()
	for ((i = 0; i < ${#PROBE_KEYS[@]}; i++)); do
		value=${got[i]#*=}
		values[${PROBE_KEYS[i]}]=$value
		case "${PROBE_KEYS[i]}" in
			sizeof\(*\))
				case "$value" in
					1 | 2 | 4 | 8) ;;
					*)
						echo "FAIL: $label: ${PROBE_KEYS[i]} is '$value', not a byte size" >&2
						return 1
						;;
				esac
				;;
		esac
	done
	[ "${values[sizeof(intptr_t)]}" = "${values[sizeof(void*)]}" ] ||
		{ echo "FAIL: $label: intptr_t is not pointer-sized" >&2; return 1; }
	[ "${values[sizeof(uintptr_t)]}" = "${values[sizeof(void*)]}" ] ||
		{ echo "FAIL: $label: uintptr_t is not pointer-sized" >&2; return 1; }
	[ "${values[sizeof(intmax_t)]}" = "${values[sizeof(uintmax_t)]}" ] ||
		{ echo "FAIL: $label: intmax_t and uintmax_t disagree" >&2; return 1; }

	value=${values[INT32_MAX]}
	case "$value" in
		'' | *[!0-9]*) echo "FAIL: $label: INT32_MAX is not a decimal value" >&2; return 1 ;;
	esac
	value=${values[UINT64_MAX]}
	if [ "${#value}" -ne 16 ]; then
		echo "FAIL: $label: UINT64_MAX is not 16 hexadecimal digits" >&2
		return 1
	fi
	case "$value" in
		*[!0-9a-f]*) echo "FAIL: $label: UINT64_MAX is not lowercase hexadecimal" >&2; return 1 ;;
	esac
	value=${values[SIZE_MAX]}
	case "$value" in
		'' | *[!0-9]*) echo "FAIL: $label: SIZE_MAX is not a decimal value" >&2; return 1 ;;
	esac
	return 0
}

# compare_outputs ABI NATIVE CROSS: fail on any differing line.
compare_outputs() {
	local abi=$1 native=$2 cross=$3
	if diff -u --label "MIPSpro $abi" --label "GCC $abi" \
		"$native" "$cross" >"$tmpdir/$abi.diff" 2>&1; then
		return 0
	fi
	echo "FAIL: $abi: GCC output differs from the MIPSpro oracle:" >&2
	cat "$tmpdir/$abi.diff" >&2
	return 1
}

# MIPSpro cannot compile the probe when the guest has no cc or no licence
# (docs/oracle.md): that is an unavailable oracle, not a toolchain failure.
native_unavailable=0
for abi in o32 n32; do
	if [ "$(cat "$tmpdir/probe.cc.$abi.unavailable")" = yes ]; then
		native_unavailable=1
		echo "note: native MIPSpro cc could not compile the $abi probe" >&2
		if [ -s "$tmpdir/probe.cc.$abi.compile" ]; then
			sed 's/^/  /' "$tmpdir/probe.cc.$abi.compile" >&2
		fi
	fi
done
if [ "$native_unavailable" -eq 1 ]; then
	skip "no oracle comparison: MIPSpro cc is unavailable (see docs/oracle.md)"
fi

failures=0
for abi in o32 n32; do
	require_run "GCC $abi probe" "probe.gcc.$abi"
	require_run "MIPSpro $abi probe" "probe.cc.$abi"
	validate_oracle "$tmpdir/probe.cc.$abi.stdout" "MIPSpro $abi" ||
		failures=$((failures + 1))
	validate_shape "$tmpdir/probe.gcc.$abi.stdout" "GCC $abi" ||
		failures=$((failures + 1))
	compare_outputs "$abi" "$tmpdir/probe.cc.$abi.stdout" \
		"$tmpdir/probe.gcc.$abi.stdout" ||
		failures=$((failures + 1))
done

if [ "$failures" -ne 0 ]; then
	echo "test-stdint-oracle: FAIL (${failures} check(s))" >&2
	exit 1
fi

echo "test-stdint-oracle: pass - o32 and n32 widths and limits match the MIPSpro oracle"
