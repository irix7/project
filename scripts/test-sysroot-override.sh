#!/usr/bin/env bash
#
# Guest-free driver-spec regression: sysroot overrides for IRIX startfiles
# and libraries (issue #29).
#
# The IRIX specs must take startfiles, ISA library directories and headers
# from the one root a link was asked for. The IRIX layer originally baked the
# configured TARGET_SYSTEM_ROOT into STARTFILE_SPEC/LIB_SPEC/ENDFILE_SPEC, so
# a command-line --sysroot changed the headers but kept the configured
# capture's startfiles and -L directories (audit C7). The fix uses the
# driver's runtime %R instead; this script fails while any configured-root
# path survives in a replacement-root trace.
#
# Three probes, all without a guest:
#   1. startfiles and -L dirs. Two empty-marker synthetic roots B and C are
#      built in a temporary directory; -### link traces for --sysroot=B and
#      --sysroot=C, for o32 and n32, must name no configured-root (A) path
#      and every sysroot-derived path must come from the requested root.
#      The toolchain's own crtbegin/crtend/irix-crti/irix-crtn files and
#      libgcc under PREFIX are the only permitted non-root paths.
#   2. headers. -E -v with --sysroot=B must search B/usr/include and not
#      A/usr/include; with probe 1 that proves headers and runtime inputs
#      converge on the same requested root.
#   3. configured-root default. With no --sysroot, o32 and n32 traces must
#      still name the expected IRIX startfiles and the configured root's
#      library directories, and, when the root is present, a trivial link
#      must produce the expected IRIX interpreter.
#
# The synthetic roots are empty marker files created by this script; nothing
# is copied out of the captured sysroot and no capture path is recorded here.
# GREEN needs a driver rebuilt from the patched series: against an
# unpatched prefix probe 1 fails on n32 by design.
#
# Usage: scripts/test-sysroot-override.sh [--prefix DIR] [--configured-root DIR]
#
#   --prefix DIR            cross prefix (default $IRIX_PREFIX, then
#                           <repo>/.scratch/toolchain-16.2.0/prefix); a missing
#                           prefix is skipped cleanly
#   --configured-root DIR   root the prefix was configured with (default
#                           $CC -print-sysroot)
#   -h, --help              show this help
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

PREFIX=${IRIX_PREFIX:-${REPO_ROOT}/.scratch/toolchain-16.2.0/prefix}
CONFIGURED_ROOT=

usage() {
	cat <<'EOF'
Usage: scripts/test-sysroot-override.sh [--prefix DIR] [--configured-root DIR]

Probe a cross driver's --sysroot handling for IRIX startfiles, libraries and
headers. See the script header for what each probe checks.

  --prefix DIR            cross prefix (default $IRIX_PREFIX, then
                          <repo>/.scratch/toolchain-16.2.0/prefix); a missing
                          prefix is skipped cleanly
  --configured-root DIR   root the prefix was configured with (default
                          $CC -print-sysroot)
  -h, --help              show this help
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
		--prefix) PREFIX=$2; shift 2 ;;
		--configured-root) CONFIGURED_ROOT=$2; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

# Skip rather than fail: the script is cheap enough to run unconditionally
# from a checkout that has not built the cross yet.
if [ ! -d "$PREFIX" ]; then
	skip "toolchain prefix not found: $PREFIX (build it with scripts/build-toolchain.sh, or pass --prefix)"
	exit 0
fi
PREFIX=$(cd "$PREFIX" && pwd -P)

CC="${PREFIX}/bin/${TARGET}-gcc"
READELF="${PREFIX}/bin/${TARGET}-readelf"
[ -x "$CC" ] || die "cross compiler not found: $CC"
[ -x "$READELF" ] || die "cross readelf not found: $READELF"

if [ -z "$CONFIGURED_ROOT" ]; then
	CONFIGURED_ROOT=$("$CC" -print-sysroot) ||
		die "cannot query the configured sysroot ($CC -print-sysroot)"
fi
[ -n "$CONFIGURED_ROOT" ] ||
	die "cross reports no configured sysroot; rebuild with scripts/build-toolchain.sh --sysroot DIR"
if [ -d "$CONFIGURED_ROOT" ]; then
	CONFIGURED_ROOT=$(cd "$CONFIGURED_ROOT" && pwd)
fi

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

printf 'int main(void) { return 0; }\n' >"$tmpdir/probe.c"
printf '#include <probe.h>\nint probe;\n' >"$tmpdir/include.c"

# make_root DIR: an empty-marker stand-in for an IRIX installation. The
# driver's -### traces only need the paths to exist in the specs, and the
# markers keep the fixture public.
make_root() {
	local root=$1 d
	mkdir -p "$root/usr/include"
	: >"$root/usr/include/probe.h"
	for d in lib usr/lib usr/lib32 usr/lib64 \
		usr/lib32/mips3 usr/lib32/mips4 \
		usr/lib64/mips3 usr/lib64/mips4; do
		mkdir -p "$root/$d"
		: >"$root/$d/crt1.o"
		: >"$root/$d/crtn.o"
		: >"$root/$d/gcrt1.o"
		: >"$root/$d/mcrt1.o"
	done
}

ROOT_B="$tmpdir/rootB"
ROOT_C="$tmpdir/rootC"
make_root "$ROOT_B"
make_root "$ROOT_C"

# The driver's final link command. -### prints one collect2 line per link.
collect2_line() {
	grep -F 'collect2' "$1" | tr '\n' ' '
}

# require_words LABEL LINE WORD...: every WORD must appear as a whole token in
# the link line.
require_words() {
	local label=$1 line=$2 word
	shift 2
	for word in "$@"; do
		case " $line " in
			*" $word "*) ;;
			*) die "$label: link trace does not name $word" ;;
		esac
	done
}

# require_names LABEL LINE NAME...: every NAME must be the last path component
# of some token (the toolchain's own startfiles are absolute PREFIX paths).
require_names() {
	local label=$1 line=$2 name tok path found
	shift 2
	for name in "$@"; do
		found=
		for tok in $line; do
			path=$tok
			case "$path" in
				-L*) path=${path#-L} ;;
			esac
			case "$path" in
				"$name"|*/"$name") found=$path; break ;;
			esac
		done
		[ -n "$found" ] || die "$label: link trace does not name $name"
	done
}

# require_root_artefact LABEL LINE NAME: some token must be a path under the
# configured root and end in NAME (o32 resolves its bare startfiles to
# $A/usr/lib32/../lib/crt1.o, n32 to $A/usr/lib32/mips3/crt1.o).
require_root_artefact() {
	local label=$1 line=$2 name=$3 tok path
	for tok in $line; do
		path=$tok
		case "$path" in
			-L*) path=${path#-L} ;;
		esac
		case "$path" in
			"$CONFIGURED_ROOT"/*"/$name") return 0 ;;
		esac
	done
	die "$label: link trace names no $name under the configured root ($CONFIGURED_ROOT)"
}

# check_root_paths LABEL LINE ROOT: every absolute path that belongs to an
# IRIX sysroot (a /usr/ or /lib* component) must be under ROOT or the
# toolchain's own PREFIX; anything else means a path leaked from another
# installation.
check_root_paths() {
	local label=$1 line=$2 root=$3 tok path
	for tok in $line; do
		path=$tok
		case "$path" in
			-L*) path=${path#-L} ;;
		esac
		case "$path" in
			/*) ;;
			*) continue ;;
		esac
		case "$path" in
			*/usr/*|*/lib/*|*/lib32/*|*/lib64/*) ;;
			*) continue ;;
		esac
		case "$path" in
			"$root"/*) continue ;;
			"$PREFIX"/*) continue ;;
		esac
		die "$label: path outside the requested root and the toolchain prefix: $path"
	done
}

# check_override_trace ABI MABI ROOT: link trace with --sysroot=ROOT must use
# ROOT for every sysroot-derived path and never mention the configured root.
check_override_trace() {
	local abi=$1 mabi=$2 root=$3 label=$4
	local trace="$tmpdir/trace.$abi.$(basename "$root")" line
	"$CC" -### --sysroot="$root" -mabi="$mabi" \
		"$tmpdir/probe.c" -o "$tmpdir/out.$abi.$(basename "$root")" \
		>"$trace" 2>&1 ||
		die "$label: driver failed to produce a link trace"
	line=$(collect2_line "$trace")
	[ -n "$line" ] || die "$label: no collect2 link command in the driver trace"
	case "$line" in
		*"$CONFIGURED_ROOT"*)
			die "$label: link trace still references the configured root ($CONFIGURED_ROOT)" ;;
	esac
	case "$line" in
		*"$root"/usr/*) ;;
		*) die "$label: link trace names no startfile or library under the requested root ($root)" ;;
	esac
	check_root_paths "$label" "$line" "$root"
}

# check_override_headers ABI MABI ROOT: the include search list must name
# ROOT/usr/include and not the configured root's.
check_override_headers() {
	local abi=$1 mabi=$2 root=$3 label=$4
	local trace="$tmpdir/headers.$abi" list
	"$CC" --sysroot="$root" -mabi="$mabi" -E -v \
		"$tmpdir/include.c" -o /dev/null >"$trace" 2>&1 ||
		die "$label: driver failed to produce a preprocess search list"
	list=$(sed -n '/#include <...> search starts here:/,/End of search list./p' "$trace")
	[ -n "$list" ] || die "$label: no include search list in the driver output"
	case "$list" in
		*"$root/usr/include"*) ;;
		*) die "$label: include search list does not name $root/usr/include" ;;
	esac
	case "$CONFIGURED_ROOT" in
		"$root") ;;
		*)
			case "$list" in
				*"$CONFIGURED_ROOT/usr/include"*)
					die "$label: include search list still references the configured root ($CONFIGURED_ROOT)" ;;
			esac
			;;
	esac
}

# Probe 1 + 2: a replacement root supplies both the runtime inputs and the
# headers.
for abi in o32 n32; do
	case "$abi" in
		o32) mabi=32 ;;
		n32) mabi=n32 ;;
	esac
	for pair in "B:$ROOT_B" "C:$ROOT_C"; do
		name=${pair%%:*}
		root=${pair#*:}
		check_override_trace "$abi" "$mabi" "$root" "$abi --sysroot=$name"
		check_override_headers "$abi" "$mabi" "$root" "$abi --sysroot=$name"
		pass "$abi --sysroot=$name: headers, startfiles and libraries all come from $name"
	done
done

# check_default_trace LABEL ABI MABI FLAGS [EXTRA PATH...]: probe 3. With no
# --sysroot the trace must name the configured root's startfiles, the
# toolchain's own crt hooks, -lc, and the caller's expected -L directories.
check_default_trace() {
	local label=$1 abi=$2 mabi=$3 flags=$4
	shift 4
	local trace="$tmpdir/trace.default.$abi" line
	# shellcheck disable=SC2086
	"$CC" -### -mabi="$mabi" $flags \
		"$tmpdir/probe.c" -o "$tmpdir/out.default.$abi" \
		>"$trace" 2>&1 ||
		die "$label: driver failed to produce a default link trace"
	line=$(collect2_line "$trace")
	[ -n "$line" ] || die "$label: no collect2 link command in the driver trace"
	case "$line" in
		*"$CONFIGURED_ROOT"*) ;;
		*) die "$label: link trace names nothing under the configured root ($CONFIGURED_ROOT)" ;;
	esac
	require_root_artefact "$label" "$line" crt1.o
	require_root_artefact "$label" "$line" crtn.o
	require_names "$label" "$line" \
		irix-crti.o irix-crtn.o crtbegin.o crtend.o
	require_words "$label" "$line" -lc "$@"
	pass "$label: default link names the configured root's IRIX startfiles"
}

check_default_trace "o32 default" o32 32 "" \
	"-L$CONFIGURED_ROOT/usr/lib32"
check_default_trace "n32 default" n32 n32 "" \
	"-L$CONFIGURED_ROOT/usr/lib32/mips3"
check_default_trace "n32 -mips4 default" n32 n32 "-mips4" \
	"-L$CONFIGURED_ROOT/usr/lib32/mips4"

if [ -d "$CONFIGURED_ROOT" ]; then
	for abi in o32 n32; do
		case "$abi" in
			o32) mabi=32; interp=/usr/lib/libc.so.1 ;;
			n32) mabi=n32; interp=/usr/lib32/libc.so.1 ;;
		esac
		"$CC" -mabi="$mabi" -c "$tmpdir/probe.c" -o "$tmpdir/$abi.o" ||
			die "$abi: compile against the configured root failed"
		"$CC" -mabi="$mabi" "$tmpdir/$abi.o" -o "$tmpdir/$abi" ||
			die "$abi: default link against the configured root failed"
		"$READELF" -l "$tmpdir/$abi" |
			grep -q "Requesting program interpreter: $interp" ||
			die "$abi: linked binary does not request the expected IRIX interpreter $interp"
		pass "$abi default link: dynamic binary with interpreter $interp"
	done
else
	skip "configured root is not present ($CONFIGURED_ROOT); not linking"
fi

echo "all sysroot override checks passed"
