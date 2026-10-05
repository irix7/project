#!/usr/bin/env bash
#
# Build the tree's hinv with a GCC cross, link it dynamically n32 against the
# captured sysroot, run it on the guest and diff its stdout against the native
# MIPSpro reference (issue #8, ADR-0005/ADR-0006).
#
# This is the first real tree command rebuilt with the new toolchain. The
# native reference in $RIG_ORACLE_DIR/hinv-reference/ fixes the flags (see
# docs/reference-hinv.md): the compile line, the rld marker and the link were
# resolved by the tree's own smake rules inside the guest. The translation
# from every MIPSpro flag to its GCC equivalent is recorded in
# docs/hinv-gcc.md; this script is its executable form. Prefer flag
# translation over source changes: the tree is stock and is never edited
# (ADR-0005).
#
# The include override is the same trick as the native build: the dev install
# lacks the kernel-only sys/EVEREST/diagval_strs.i and diskinvent.h, so the
# tree's irix/kern and irix/usr/include are added ahead of the sysroot's
# /usr/include. -nostdinc is not carried over: --sysroot gives GCC the
# sysroot's headers in the same position -I//usr/include held in the MIPSpro
# line, while GCC's own include directory stays available.
#
# The ABI is the tree's own default for this release: n32 (-mips3 -mabi=n32),
# dynamically linked, with the MIPSpro rld marker translated literally to the
# GNU ld spelling (-Wl,-I,/lib32/libc.so.1,-rpath,/lib32), so PT_INTERP and
# RPATH match the reference.
#
# Artefacts are evidence, not deliverables: they stay under .scratch/hinv-gcc
# (or --out) and are never committed. The native reference and the pulled
# guest output are SGI material and stay outside the repo (ADR-0001).
#
# Usage:
#   scripts/rig/hinv-gcc.sh --tree /path/to/irix-6.5.7m-src --no-run
#   scripts/rig/hinv-gcc.sh --prefix .scratch/toolchain/prefix   # bring-up
#   scripts/rig/hinv-gcc.sh                                      # acceptance
#
# Every guest transaction takes the rig's shared guest lock, mirroring
# smoke.sh; do not wrap the whole script in that same lock.
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)

# The acceptance cross is #6's GCC 16.2 prefix; --prefix overrides it, which
# is what the 15.2 bring-up datapoint uses.
PREFIX=${HINV_GCC_PREFIX:-${REPO_ROOT}/.scratch/toolchain-16.2.0/prefix}
SYSROOT=${HINV_GCC_SYSROOT:-}
TREE=${IRIX_SRC_TREE:-/home/matt/projects/irix-6.5.7m-src}
OUT=${HINV_GCC_OUT:-${REPO_ROOT}/.scratch/hinv-gcc}
ORACLE_DIR=${RIG_ORACLE_DIR:-/mnt/europa/sgi-toolchain-scratch/rig/oracle}
TIMEOUT=${HINV_GCC_TIMEOUT:-600}
RUN=1
PRINT_COMMANDS=0

usage() {
	cat <<'EOF'
Usage: scripts/rig/hinv-gcc.sh [options]

Cross-build the tree's irix/cmd/hinv/hinv.c, link it dynamically n32 against
the captured sysroot, optionally run it on the guest and diff its stdout
against $RIG_ORACLE_DIR/hinv-reference/hinv.output.

  --prefix DIR     cross prefix (default <repo>/.scratch/toolchain-16.2.0/prefix)
  --sysroot DIR    sysroot to pass to GCC; default is the cross's configured
                   sysroot (gcc -print-sysroot)
  --tree DIR       IRIX source checkout (default $IRIX_SRC_TREE or
                   /home/matt/projects/irix-6.5.7m-src)
  --out DIR        evidence directory (default <repo>/.scratch/hinv-gcc)
  --no-run         build and check the ELF shape only; do not touch the guest
  --run            run on the guest and diff (default)
  --timeout SEC    timeout for each iris-ci call (default 600)
  --print-commands print the translated compile and link commands and exit
                   (host-only; needs an explicit --sysroot)
  -h, --help       show this help

The build commands, compiler diagnostics, readelf dumps and guest streams are
kept under --out; the guest run is one transaction under the rig's shared
guest lock. Exit status is non-zero on any compile, link, dynamic-link or
stdout mismatch.
EOF
}

die() {
	echo "hinv-gcc: $*" >&2
	exit 1
}

while [ $# -gt 0 ]; do
	case "$1" in
		--prefix) PREFIX=$2; shift 2 ;;
		--sysroot) SYSROOT=$2; shift 2 ;;
		--tree) TREE=$2; shift 2 ;;
		--out) OUT=$2; shift 2 ;;
		--timeout) TIMEOUT=$2; shift 2 ;;
		--no-run) RUN=0; shift ;;
		--run) RUN=1; shift ;;
		--print-commands) PRINT_COMMANDS=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) usage >&2; die "unknown option: $1" ;;
	esac
done

CC="$PREFIX/bin/${TARGET}-gcc"
READELF="$PREFIX/bin/${TARGET}-readelf"

# The translated flags. Each line here has a row in docs/hinv-gcc.md; the
# MIPSpro original is in the comments so the two stay legible side by side.
#
# MIPSpro: -mips3 -n32 -O  ->  keep the ISA and ABI, GCC spelling.
#   -O stays -O even though MIPSpro's driver splits it to the back end's -O2:
#   matching optimiser intensity is not required for behavioural equivalence
#   and the divergence is recorded.
# -std=gnu89 is not a translation but the mode the 1990s tree code needs
#   under GCC 16.2, which otherwise rejects implicit int/declarations.
# -MD is the optional translation of -MDupdate Makedepend, kept so the
#   dependency record is captured next to the object.
# -woff ... has no GCC numbering equivalent and is dropped; GCC diagnostics
#   are reviewed in the compile log and none are errors.
# -quickstart_info, -nostdlib and -L//usr/lib32/... are dropped: the GCC IRIX
#   specs supply crt1.o, libc and the sysroot search paths.
# -Wl,-I,/lib32/libc.so.1,-rpath,/lib32 translates literally; GNU ld's -I is
#   the dynamic-linker selector, the same meaning MIPSpro's -I carries.
INCLUDES=(-I"$TREE/irix/kern" -I"$TREE/irix/usr/include")
CFLAGS=(-mips3 -mabi=n32 -O -std=gnu89 -MD "${INCLUDES[@]}")
LDFLAGS=(-mips3 -mabi=n32 -O -Wl,-I,/lib32/libc.so.1 -Wl,-rpath,/lib32)
SRC="$TREE/irix/cmd/hinv/hinv.c"
OBJ="$OUT/hinv.o"
BIN="$OUT/hinv"

# Quote one word only when it needs it, so the printed commands stay
# copy-pasteable (bash's %q escapes commas, which mangles -Wl options).
quote() {
	case "$1" in
		*[!A-Za-z0-9_./,=:@%+-]*) printf "'%s'" "${1//\'/\'\\\'\'}" ;;
		*) printf '%s' "$1" ;;
	esac
}

print_cmd() {
	local first=1 word
	for word in "$@"; do
		if [ "$first" -eq 1 ]; then first=0; else printf ' '; fi
		quote "$word"
	done
	printf '\n'
}

# In --print-commands mode the script is pure: no prefix, tree or sysroot
# needs to exist, so the flag translation can be unit-tested on any host.
# --sysroot is required because resolving it needs the compiler.
if [ "$PRINT_COMMANDS" -eq 1 ]; then
	[ -n "$SYSROOT" ] || die "--print-commands needs --sysroot DIR"
	printf 'compile: '
	print_cmd "$CC" "--sysroot=$SYSROOT" "${CFLAGS[@]}" -c "$SRC" -o "$OBJ"
	printf 'link: '
	print_cmd "$CC" "--sysroot=$SYSROOT" "${LDFLAGS[@]}" -o "$BIN" "$OBJ"
	exit 0
fi

[ -d "$PREFIX" ] || die "toolchain prefix not found: $PREFIX (run scripts/build-toolchain.sh, or wait for #6's .scratch/toolchain-16.2.0/READY)"
[ -x "$CC" ] || die "cross compiler not found: $CC"
[ -x "$READELF" ] || die "cross readelf not found: $READELF"
[ -f "$SRC" ] || die "tree source not found: $SRC (pass --tree DIR)"

# Resolve the sysroot exactly as smoke.sh does: the one the cross was
# configured with, unless --sysroot overrides it.
if [ -n "$SYSROOT" ]; then
	[ -d "$SYSROOT" ] || die "sysroot not found: $SYSROOT"
	SYSROOT=$(cd "$SYSROOT" && pwd)
else
	SYSROOT=$("$CC" -print-sysroot)
	[ -n "$SYSROOT" ] ||
		die "cross has no sysroot configured ($CC -print-sysroot is empty)"
fi
[ -d "$SYSROOT" ] || die "sysroot does not exist: $SYSROOT"
[ -n "$(ls -A "$SYSROOT" 2>/dev/null || true)" ] || die "sysroot is empty: $SYSROOT"
[ -d "$SYSROOT/usr/include" ] ||
	die "sysroot has no headers: $SYSROOT/usr/include (capture is incomplete)"

mkdir -p "$OUT"

# The exact command lines, for the record and for the unit test.
{
	printf 'compile: '
	print_cmd "$CC" "--sysroot=$SYSROOT" "${CFLAGS[@]}" -c "$SRC" -o "$OBJ"
	printf 'link: '
	print_cmd "$CC" "--sysroot=$SYSROOT" "${LDFLAGS[@]}" -o "$BIN" "$OBJ"
	printf 'sysroot: %s\ntree: %s\nprefix: %s\n' "$SYSROOT" "$TREE" "$PREFIX"
} >"$OUT/build-commands.txt"

"$CC" --version >"$OUT/gcc-version.txt" 2>&1
gcc_first_line=$(head -n 1 "$OUT/gcc-version.txt")
echo "hinv-gcc: compiler: $gcc_first_line"

echo "hinv-gcc: compiling $SRC"
[ -f "$TREE/irix/kern/sys/EVEREST/diagval_strs.i" ] ||
	die "tree header missing: $TREE/irix/kern/sys/EVEREST/diagval_strs.i"
[ -f "$TREE/irix/usr/include/diskinvent.h" ] ||
	die "tree header missing: $TREE/irix/usr/include/diskinvent.h"

if ! "$CC" "--sysroot=$SYSROOT" "${CFLAGS[@]}" -c "$SRC" -o "$OBJ" \
	>"$OUT/hinv.compile.log" 2>&1; then
	cat "$OUT/hinv.compile.log" >&2
	die "compile failed; log in $OUT/hinv.compile.log"
fi
cat "$OUT/hinv.compile.log"

echo "hinv-gcc: linking $BIN"
if ! "$CC" "--sysroot=$SYSROOT" "${LDFLAGS[@]}" -o "$BIN" "$OBJ" \
	>"$OUT/hinv.link.log" 2>&1; then
	cat "$OUT/hinv.link.log" >&2
	die "link failed; log in $OUT/hinv.link.log"
fi
cat "$OUT/hinv.link.log"

# Dynamic by design (ADR-0006), and n32/mips3 by the tree's default. This is
# the same PT_INTERP proof smoke.sh uses; the ELF shape is then recorded
# beside the native reference's for triage.
"$READELF" -h -l -d -S "$BIN" >"$OUT/hinv.readelf.txt"
grep -q 'INTERP' "$OUT/hinv.readelf.txt" ||
	die "linked binary is not dynamically linked (no PT_INTERP); ADR-0006 requires the dynamic model"
grep -q 'Class:.*ELF32' "$OUT/hinv.readelf.txt" || die "not ELF32: n32 expected"
grep -q "big endian" "$OUT/hinv.readelf.txt" || die "not big-endian"
grep -q 'abi2' "$OUT/hinv.readelf.txt" || die "no EF_MIPS_ABI2 in flags: n32 expected"
grep -q 'mips3' "$OUT/hinv.readelf.txt" || die "not MIPS III in flags: -mips3 expected"
"$READELF" -h -S "$OBJ" >"$OUT/hinv.o.readelf.txt"

ref_bin="$ORACLE_DIR/hinv-reference/hinv"
ref_obj="$ORACLE_DIR/hinv-reference/hinv.o"
if [ -f "$ref_bin" ] && [ -f "$ref_obj" ]; then
	"$READELF" -h -l -d -S "$ref_bin" >"$OUT/reference.hinv.readelf.txt"
	"$READELF" -h -S "$ref_obj" >"$OUT/reference.hinv.o.readelf.txt"
	diff -u "$OUT/reference.hinv.readelf.txt" "$OUT/hinv.readelf.txt" \
		>"$OUT/hinv.elf-shape.diff" || true
	echo "hinv-gcc: ELF shape vs reference recorded in $OUT/hinv.elf-shape.diff"
fi

if [ "$RUN" -eq 0 ]; then
	echo "hinv-gcc: built $BIN (guest run skipped)"
	exit 0
fi

# --- guest run ------------------------------------------------------------
REF_OUT="$ORACLE_DIR/hinv-reference/hinv.output"
[ -f "$REF_OUT" ] || die "reference output not found: $REF_OUT"

# shellcheck source=scripts/rig/lib.sh
source "$REPO_ROOT/scripts/rig/lib.sh"
rig_require_iris
rig_running || die "rig is not running (no answer on $RIG_SOCKET); start it with scripts/rig/start-rig.sh"

guest_name="hinv-gcc-$$-$RANDOM"
guest_bin="/tmp/hinv-gcc/$guest_name"
host_out="$OUT/hinv.guest.output"
host_err="$OUT/hinv.guest.stderr"
host_status="$OUT/hinv.guest.status"
host_file="$OUT/hinv.guest.file"

# The guest command lives in a shipped script rather than on the run line:
# the guest's serial input silently wedges on long command lines
# (docs/reference-hinv.md), and the smoke-harness shape with sh -c inline was
# long enough to hit that. The script separates stdout/stderr/status, then
# records the guest's own file(1) ABI proof.
runner="$OUT/guest-run.sh"
cat >"$runner" <<RUN
#!/bin/sh
B='$guest_bin'
chmod +x "\$B"
"\$B" > "\$B.stdout" 2> "\$B.stderr"
echo \$? > "\$B.status"
file "\$B" > "\$B.file" 2>&1
RUN
chmod +x "$runner"

# The same one-transaction shape as smoke.sh: setup, ship, run, then pull
# every stream. It runs under the rig's guest lock so it serialises with every
# other client.
txn="$OUT/guest-txn.sh"
cat >"$txn" <<'TXN'
#!/usr/bin/env bash
#
# One hinv-gcc guest transaction. Generated per run by scripts/rig/hinv-gcc.sh
# and executed under flock on $RIG_DIR/guest.lock.
#
set -euo pipefail

binary=$1
guest_bin=$2
host_out=$3
host_err=$4
host_status=$5
host_file=$6
timeout=$7
repo_root=$8
runner=$9

# shellcheck source=scripts/rig/lib.sh
source "$repo_root/scripts/rig/lib.sh"

ic -q run "mkdir -p /tmp/hinv-gcc" --timeout "$timeout" >/dev/null
ic -q put "$binary" --to "$guest_bin" --timeout "$timeout" >/dev/null
ic -q put "$runner" --to "$guest_bin.run" --timeout "$timeout" >/dev/null

run_status=0
ic -q run "sh $guest_bin.run" --timeout "$timeout" >/dev/null || run_status=$?

ic -q get "$guest_bin.stdout" --to "$host_out" --timeout "$timeout" >/dev/null || true
ic -q get "$guest_bin.stderr" --to "$host_err" --timeout "$timeout" >/dev/null || true
ic -q get "$guest_bin.status" --to "$host_status" --timeout "$timeout" >/dev/null || true
ic -q get "$guest_bin.file" --to "$host_file" --timeout "$timeout" >/dev/null || true

[ "$run_status" -eq 0 ] || exit 90
exit 0
TXN

rm -f "$host_out" "$host_err" "$host_status" "$host_file"
txn_status=0
rig_with_guest_lock bash "$txn" \
	"$BIN" "$guest_bin" "$host_out" "$host_err" "$host_status" "$host_file" \
	"$TIMEOUT" "$REPO_ROOT" "$runner" ||
	txn_status=$?

[ -f "$host_out" ] && [ -f "$host_status" ] ||
	die "guest transaction did not complete (status $txn_status); messages above"

guest_rc=$(cat "$host_status")
case "$guest_rc" in
	''|*[!0-9]*) die "guest returned an unreadable exit status: '$guest_rc'" ;;
esac

if [ "$guest_rc" -ne 0 ]; then
	echo "hinv-gcc: guest program exited $guest_rc, expected 0" >&2
	if [ -s "$host_out" ]; then cat "$host_out" >&2; fi
	if [ -s "$host_err" ]; then cat "$host_err" >&2; fi
	exit 1
fi

if [ -s "$host_err" ]; then cat "$host_err" >&2; fi

if ! diff -u "$REF_OUT" "$host_out" >"$OUT/hinv.output.diff"; then
	cat "$OUT/hinv.output.diff" >&2
	die "guest stdout does not match $REF_OUT"
fi
rm -f "$OUT/hinv.output.diff"

if [ -s "$host_file" ]; then
	guest_abi=$(cat "$host_file")
	echo "hinv-gcc: guest $guest_abi"
	case "$guest_abi" in
		*"ELF N32 MSB mips-3"*dynamic*) ;;
		*) die "guest file(1) does not identify an n32 MSB mips-3 dynamic executable: $guest_abi" ;;
	esac
fi

echo "hinv-gcc: pass - $gcc_first_line"
echo "hinv-gcc: output matches $REF_OUT byte-for-byte"
cat "$host_out"
