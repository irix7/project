#!/usr/bin/env bash
#
# Smoke harness: compile, link, ship, run and diff (issue #5).
#
# The project's primary testing seam. One command takes a C source and the
# exact stdout it must produce, compiles and links it with the cross under
# test configured against the captured IRIX sysroot, ships the binary to the
# guest with iris-ci, runs it there and diffs the guest's stdout against the
# expected file.
#
# Linking is dynamic by design (ADR-0006): IRIX 6.5 ships no static libc, so
# the cross links SGI's crt1.o, libc.so and libm.so from the captured sysroot.
# The harness refuses a binary without PT_INTERP, which keeps a future
# -static case (the rebuilt runtime's proof, #9) out of this seam.
#
# The ABI defaults to o32, the Indy's native environment (ADR-0003).
# --cflags is the seam later milestones and #18 use: it is passed to both the
# compile and the link step, so -O2, -pthread and -lm need no script change.
#
# Usage: scripts/smoke.sh [options] SOURCE EXPECTED
#
#   --prefix DIR     cross prefix (default <repo>/.scratch/toolchain-16.2.0/prefix)
#   --sysroot DIR    sysroot to check and pass to GCC; default is the cross's
#                    configured sysroot (gcc -print-sysroot)
#   --abi ABI        o32 (default) or n32; mapped to -mabi=32|n32
#   --cflags FLAGS   extra flags, passed at compile and link (word-split)
#   --timeout SEC    timeout for each iris-ci call (default 300)
#   -h, --help       show this help
#
# Preconditions, each failing non-zero with a clear message: source/expected
# files exist, the cross exists, its sysroot is configured and non-empty with
# headers, and the rig answers. The whole guest transaction (mkdir + put +
# run + get) runs under lib.sh's shared guest lock, so the harness serialises
# with any client that takes the same lock; guest files live under /tmp/smoke.
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

PREFIX=${SMOKE_PREFIX:-${REPO_ROOT}/.scratch/toolchain-16.2.0/prefix}
SYSROOT=
ABI=o32
CFLAGS=
TIMEOUT=300
SOURCE=
EXPECTED=

usage() {
	cat <<'EOF'
Usage: scripts/smoke.sh [options] SOURCE EXPECTED

Compile SOURCE with the cross under test, link it dynamically against the
captured sysroot, run it on the IRIX guest and diff its stdout against
EXPECTED.

  --prefix DIR     cross prefix (default <repo>/.scratch/toolchain-16.2.0/prefix)
  --sysroot DIR    sysroot to check and pass to GCC; default is the cross's
                   configured sysroot (gcc -print-sysroot)
  --abi ABI        o32 (default) or n32; mapped to -mabi=32|n32
  --cflags FLAGS   extra flags, passed at compile and link (word-split)
  --timeout SEC    timeout for each iris-ci call (default 300)
  -h, --help       show this help

On success the program's stdout is printed and the exit status is 0. A
compile or link failure, a non-zero guest exit or a stdout mismatch exits
non-zero; mismatches print a unified diff.
EOF
}

die() {
	echo "smoke: $*" >&2
	exit 1
}

while [ $# -gt 0 ]; do
	case "$1" in
		--prefix) PREFIX=$2; shift 2 ;;
		--sysroot) SYSROOT=$2; shift 2 ;;
		--abi) ABI=$2; shift 2 ;;
		--cflags) CFLAGS=$2; shift 2 ;;
		--timeout) TIMEOUT=$2; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		-*) die "unknown option: $1 (try --help)" ;;
		*) break ;;
	esac
done

SOURCE=${1:-}
EXPECTED=${2:-}
[ $# -le 2 ] || die "unexpected extra argument: ${3} (try --help)"
[ -n "$SOURCE" ] && [ -n "$EXPECTED" ] || {
	usage >&2
	die "SOURCE and EXPECTED are required"
}

# Fail on cheap, local problems before touching the guest. The ABI check is
# deliberately early so a typo does not wait on the guest lock.
case "$ABI" in
	o32) MABI=32 ;;
	n32) MABI=n32 ;;
	*) die "unknown ABI: $ABI (expected o32 or n32; n64 is out of scope, ADR-0003)" ;;
esac

[ -f "$SOURCE" ] || die "source not found: $SOURCE"
[ -f "$EXPECTED" ] || die "expected output not found: $EXPECTED"
[ -d "$PREFIX" ] || die "toolchain prefix not found: $PREFIX (run scripts/build-toolchain.sh)"

CC="$PREFIX/bin/${TARGET}-gcc"
READELF="$PREFIX/bin/${TARGET}-readelf"
[ -x "$CC" ] || die "cross compiler not found: $CC"
[ -x "$READELF" ] || die "cross readelf not found: $READELF"

# The default sysroot is the one the cross was configured with; that is the
# whole point of the build-toolchain.sh --sysroot rebuild. --sysroot overrides
# and is passed through as a relocation.
sysroot_arg=()
if [ -n "$SYSROOT" ]; then
	[ -d "$SYSROOT" ] || die "sysroot not found: $SYSROOT"
	SYSROOT=$(cd "$SYSROOT" && pwd)
	sysroot_arg=("--sysroot=$SYSROOT")
else
	SYSROOT=$("$CC" -print-sysroot)
	[ -n "$SYSROOT" ] ||
		die "cross has no sysroot configured ($CC -print-sysroot is empty); rebuild with scripts/build-toolchain.sh --sysroot DIR"
fi
[ -d "$SYSROOT" ] || die "sysroot does not exist: $SYSROOT"
[ -n "$(ls -A "$SYSROOT" 2>/dev/null || true)" ] || die "sysroot is empty: $SYSROOT"
[ -d "$SYSROOT/usr/include" ] ||
	die "sysroot has no headers: $SYSROOT/usr/include (capture is incomplete)"

cflags=()
if [ -n "$CFLAGS" ]; then
	read -r -a cflags <<<"$CFLAGS"
fi

# shellcheck source=scripts/rig/lib.sh
source "$REPO_ROOT/scripts/rig/lib.sh"
rig_require_iris
rig_running || die "rig is not running (no answer on $RIG_SOCKET); start it with scripts/rig/start-rig.sh"

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

stem=$(basename "$SOURCE")
stem=${stem%.*}
obj="$tmpdir/${stem}.o"
binary="$tmpdir/${stem}"

"$CC" "${sysroot_arg[@]}" -mabi="$MABI" "${cflags[@]}" -c "$SOURCE" -o "$obj" ||
	die "compile failed: $SOURCE"
"$CC" "${sysroot_arg[@]}" -mabi="$MABI" -o "$binary" "$obj" "${cflags[@]}" ||
	die "link failed: $stem"

"$READELF" -l "$binary" >"$tmpdir/phdr" ||
	die "readelf failed on the linked binary: $stem"
grep -q 'INTERP' "$tmpdir/phdr" ||
	die "linked binary is not dynamically linked (no PT_INTERP); ADR-0006 requires the dynamic model"

# The guest transaction is one unit: setup, ship, run, retrieve. It runs
# under the rig's guest lock so concurrent clients (agents, later milestones)
# cannot interleave commands or clobber /tmp/smoke.
guest_name="smoke-$$-$RANDOM"
guest_bin="/tmp/smoke/$guest_name"
host_out="$tmpdir/${stem}.stdout"
host_err="$tmpdir/${stem}.stderr"
host_status="$tmpdir/${stem}.status"

txn="$tmpdir/guest-txn.sh"
cat >"$txn" <<'TXN'
#!/usr/bin/env bash
#
# One smoke-harness guest transaction. Generated per run by scripts/smoke.sh
# and executed under flock on $RIG_DIR/guest.lock.
#
set -euo pipefail

binary=$1
guest_bin=$2
host_out=$3
host_err=$4
host_status=$5
timeout=$6
repo_root=$7

# shellcheck source=scripts/rig/lib.sh
source "$repo_root/scripts/rig/lib.sh"

ic -q run "mkdir -p /tmp/smoke" --timeout "$timeout" >/dev/null
ic -q put "$binary" --to "$guest_bin" --timeout "$timeout" >/dev/null

# Run through sh so stdout and stderr can be captured separately (csh's
# redirections differ) and so the program's own exit status can be written to
# a file: iris-ci only reports its own status, and on guest failure it prints
# the command rather than the program's stdout. sh creates the redirection
# targets before exec, so the files exist even when the program cannot start;
# the trailing echo always runs and leaves the true status behind.
run_status=0
ic -q run "sh -c 'chmod +x $guest_bin; $guest_bin > $guest_bin.stdout 2> $guest_bin.stderr; echo \$? > $guest_bin.status'" \
	--timeout "$timeout" >/dev/null || run_status=$?

ic -q get "$guest_bin.stdout" --to "$host_out" --timeout "$timeout" >/dev/null || true
ic -q get "$guest_bin.stderr" --to "$host_err" --timeout "$timeout" >/dev/null || true
ic -q get "$guest_bin.status" --to "$host_status" --timeout "$timeout" >/dev/null || true

# A non-zero iris-ci status means the transaction broke, not the program;
# 90 tells smoke.sh the difference.
[ "$run_status" -eq 0 ] || exit 90
exit 0
TXN

txn_status=0
rig_with_guest_lock bash "$txn" \
	"$binary" "$guest_bin" "$host_out" "$host_err" "$host_status" "$TIMEOUT" "$REPO_ROOT" ||
	txn_status=$?

# Missing stream or status files mean the transaction itself broke (ship or
# retrieve), not that the program printed nothing: sh creates them before exec.
[ -f "$host_out" ] && [ -f "$host_status" ] ||
	die "guest transaction did not complete (status $txn_status); messages above"

guest_rc=$(cat "$host_status")
case "$guest_rc" in
	''|*[!0-9]*) die "guest returned an unreadable exit status: '$guest_rc'" ;;
esac

if [ "$guest_rc" -ne 0 ]; then
	echo "smoke: guest program exited $guest_rc, expected 0" >&2
	if [ -s "$host_out" ]; then
		printf -- '--- guest stdout ---\n' >&2
		cat "$host_out" >&2
	fi
	if [ -s "$host_err" ]; then
		printf -- '--- guest stderr ---\n' >&2
		cat "$host_err" >&2
	fi
	exit 1
fi

# Diagnostic stderr never affects the stdout comparison.
if [ -s "$host_err" ]; then
	cat "$host_err" >&2
fi

if ! diff -u "$EXPECTED" "$host_out"; then
	die "stdout does not match $EXPECTED"
fi

cat "$host_out"
