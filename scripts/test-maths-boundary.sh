#!/usr/bin/env bash
#
# Host-side proof that the maths-boundary fixture forces real libm calls
# (issue #5, audit C10).
#
# The oracle's hello.c calls sqrt(2.0), a constant the compiler may fold, so
# it does not establish that the dynamic library boundary is crossed.
# scripts/smoke/maths-boundary.c takes its input from argv; this script
# compiles it with -O2 for o32 and n32 and asserts, through the cross's own
# readelf, that:
#
#   * sin and sqrt are still undefined external symbols in the object, so no
#     constant folding removed the calls;
#   * the object carries external R_MIPS_CALL16 relocations naming them;
#   * the linked binary has PT_INTERP and a NEEDED libm.so entry, so a guest
#     run executes against the captured dynamic library.
#
# No guest is touched: the controlled guest runs are a separate, recorded
# integrator step (docs/smoke.md, "The maths boundary").
#
# Usage: scripts/test-maths-boundary.sh [--prefix DIR]
#
#   --prefix DIR   cross prefix (default $IRIX_PREFIX, then
#                  <repo>/.scratch/toolchain-16.2.0/prefix); a missing prefix
#                  is skipped cleanly
#   -h, --help     show this help
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
FIXTURE="${REPO_ROOT}/scripts/smoke/maths-boundary.c"
EXPECTED="${REPO_ROOT}/scripts/smoke/maths-boundary.expected"

PREFIX=${IRIX_PREFIX:-${REPO_ROOT}/.scratch/toolchain-16.2.0/prefix}

usage() {
	cat <<'EOF'
Usage: scripts/test-maths-boundary.sh [--prefix DIR]

Compile scripts/smoke/maths-boundary.c with -O2 for o32 and n32 and assert,
with readelf, that sin and sqrt remain external symbols with R_MIPS_CALL16
relocations and that the linked binaries need libm.so. No guest is touched.

  --prefix DIR   cross prefix (default $IRIX_PREFIX, then
                 <repo>/.scratch/toolchain-16.2.0/prefix); a missing prefix
                 is skipped cleanly
  -h, --help     show this help
EOF
}

die() {
	echo "FAIL: $*" >&2
	exit 1
}

pass() {
	echo "ok: $*"
}

skip() {
	echo "skip: $*"
}

while [ $# -gt 0 ]; do
	case "$1" in
		--prefix)
			PREFIX=$2
			shift 2
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			die "unknown option: $1 (try --help)"
			;;
	esac
done

[ -f "$FIXTURE" ] || die "fixture not found: $FIXTURE"
[ -f "$EXPECTED" ] || die "expected output not found: $EXPECTED"

# Skip rather than fail: cheap enough to run from a checkout whose cross has
# not been built yet.
if [ ! -d "$PREFIX" ]; then
	skip "toolchain prefix not found: $PREFIX (build it with scripts/build-toolchain.sh, or pass --prefix)"
	exit 0
fi
PREFIX=$(cd "$PREFIX" && pwd)

CC="${PREFIX}/bin/${TARGET}-gcc"
READELF="${PREFIX}/bin/${TARGET}-readelf"
[ -x "$CC" ] || die "cross compiler not found: $CC"
[ -x "$READELF" ] || die "cross readelf not found: $READELF"

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# The fixture's no-argument input is 2.0; the recorded output must match what
# the C program prints for it. Checked with host Python arithmetic, not by
# running a binary.
python3 - <<'PY' >"$tmpdir/expected.python"
import math
print("sin(2.000000) = %.6f" % math.sin(2.0))
print("sqrt(2.000000) = %.6f" % math.sqrt(2.0))
PY
if ! diff -u "$EXPECTED" "$tmpdir/expected.python" >/dev/null; then
	die "$EXPECTED does not match the fixture's no-argument output"
fi
pass "expected output matches the fixture's no-argument input"

check_abi() {
	local abi=$1 mabi=$2
	local obj="$tmpdir/maths.$abi.o" bin="$tmpdir/maths.$abi"
	local syms relocs fn

	"$CC" -mabi="$mabi" -O2 -c "$FIXTURE" -o "$obj" ||
		die "$abi: compile of $FIXTURE failed"

	syms=$("$READELF" -s "$obj") ||
		die "$abi: readelf -s failed on the -O2 object"
	for fn in sin sqrt; do
		printf '%s\n' "$syms" | grep -Eq "UND[[:space:]]+$fn$" ||
			die "$abi: $fn is not an undefined external symbol after -O2 (constant folded?)"
	done

	relocs=$("$READELF" -r "$obj") ||
		die "$abi: readelf -r failed on the -O2 object"
	for fn in sin sqrt; do
		printf '%s\n' "$relocs" |
			grep -Eq "R_MIPS_CALL16.*[[:space:]]$fn( \+ 0)?$" ||
			die "$abi: no external R_MIPS_CALL16 relocation to $fn in the object"
	done

	"$CC" -mabi="$mabi" -O2 "$obj" -lm -o "$bin" ||
		die "$abi: link with -lm failed"

	"$READELF" -l "$bin" | grep -q 'INTERP' ||
		die "$abi: linked binary is not dynamic (no PT_INTERP)"
	"$READELF" -d "$bin" | grep -Eq 'NEEDED.*libm\.so' ||
		die "$abi: linked binary has no NEEDED libm.so entry"

	pass "$abi: sin and sqrt external, R_MIPS_CALL16 relocations, NEEDED libm.so"
}

check_abi o32 32
check_abi n32 n32

echo "all maths-boundary checks passed"
