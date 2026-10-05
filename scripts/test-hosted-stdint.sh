#!/usr/bin/env bash
#
# Hosted <stdint.h> policy test for the mips-sgi-irix6.5 cross (issue #30).
#
# GCC selects the cross's <stdint.h> policy in config.gcc's use_gcc_stdint:
# `provide` installs ginclude/stdint-gcc.h directly, `wrap` installs
# stdint-wrap.h, which #include_next's the target's stdint.h when hosted.
# The captured 6.5.7m environment has no /usr/include/stdint.h (it ships
# inttypes.h, and only the optional IRIX Development Foundation 1.3 provides
# stdint.h), so wrap leaves a hosted #include <stdint.h> unresolvable for
# o32 and n32; provide is the policy that matches the actual capture.
#
# The probes are guest-free: they compile synthetic sources with the
# installed cross and assert the integer model of each ABI. The capture's
# own headers are read in place at compile time; no SGI header text is
# copied into this repository.
#
#   A  hosted <stdint.h>, o32 and n32, with per-ABI _Static_assert checks
#   B  include-order with the capture's <inttypes.h>, both directions
#   C  C++ consumer including both headers, when the cross has cc1plus
#   D  freestanding <stdint.h>, which must keep working
#
# The sysroot is resolved like scripts/smoke.sh: --sysroot wins, otherwise
# the cross's configured sysroot (gcc -print-sysroot). Probes B and C need
# the capture's inttypes.h and are skipped when no sysroot is available;
# probe C is also skipped for a C-only cross. A missing prefix skips the
# whole run so an unbuilt tree does not fail the suite.
#
# Usage: scripts/test-hosted-stdint.sh [--prefix DIR] [--sysroot DIR]
#
set -euo pipefail

TARGET=mips-sgi-irix6.5

PREFIX=${IRIX_PREFIX:-${PWD}/.scratch/toolchain-16.2/prefix}
SYSROOT=
SYSROOT_EXPLICIT=0

usage() {
	cat <<'EOF'
Usage: scripts/test-hosted-stdint.sh [--prefix DIR] [--sysroot DIR]

Compile hosted, include-order, C++ (when available) and freestanding
<stdint.h> probes for the IRIX 6.5 cross under test.

  --prefix DIR     cross installation prefix
                   (default $IRIX_PREFIX or $PWD/.scratch/toolchain-16.2/prefix)
  --sysroot DIR    capture to compile the native-header probes against
                   (default: gcc -print-sysroot)
  -h, --help       show this help

Sysroot-dependent probes are skipped, with a message, when no sysroot is
available. A missing prefix skips the run.
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
		--sysroot) SYSROOT=$2; SYSROOT_EXPLICIT=1; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

if [ ! -d "$PREFIX" ]; then
	skip "toolchain prefix not found: $PREFIX"
	exit 0
fi

CC="${PREFIX}/bin/${TARGET}-gcc"
if [ ! -x "$CC" ]; then
	skip "cross compiler not found: ${CC} (toolchain not built?)"
	exit 0
fi

if [ "$SYSROOT_EXPLICIT" -eq 1 ]; then
	[ -d "$SYSROOT" ] || die "sysroot not found: $SYSROOT"
	SYSROOT=$(cd "$SYSROOT" && pwd)
else
	SYSROOT=$("$CC" -print-sysroot)
fi

sysroot_arg=()
have_native_headers=0
if [ -n "$SYSROOT" ] && [ -d "$SYSROOT" ]; then
	sysroot_arg=("--sysroot=$SYSROOT")
	if [ -f "$SYSROOT/usr/include/inttypes.h" ]; then
		have_native_headers=1
	fi
fi

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

failures=0

# check LABEL COMMAND...: run a probe, print the compiler diagnostics on
# failure, and keep going so one RED run shows every broken probe.
check() {
	local label=$1 out=${tmpdir}/probe.out
	shift
	if "$@" >"$out" 2>&1; then
		pass "$label"
	else
		echo "FAIL: $label" >&2
		cat "$out" >&2
		failures=$((failures + 1))
	fi
}

PTR_SIZE=4

# --- probe A/D source: <stdint.h> with the o32/n32 integer model ----------
cat > "${tmpdir}/stdint.c" <<'EOF'
#include <stdint.h>

#ifndef EXPECT_PTR_SIZE
#error "the harness must define EXPECT_PTR_SIZE"
#endif

_Static_assert(sizeof(int8_t) == 1, "int8_t is not 1 byte");
_Static_assert(sizeof(int16_t) == 2, "int16_t is not 2 bytes");
_Static_assert(sizeof(int32_t) == 4, "int32_t is not 4 bytes");
_Static_assert(sizeof(int64_t) == 8, "int64_t is not 8 bytes");
_Static_assert(sizeof(uint8_t) == 1, "uint8_t is not 1 byte");
_Static_assert(sizeof(uint16_t) == 2, "uint16_t is not 2 bytes");
_Static_assert(sizeof(uint32_t) == 4, "uint32_t is not 4 bytes");
_Static_assert(sizeof(uint64_t) == 8, "uint64_t is not 8 bytes");

_Static_assert(sizeof(intptr_t) == EXPECT_PTR_SIZE, "intptr_t width");
_Static_assert(sizeof(uintptr_t) == EXPECT_PTR_SIZE, "uintptr_t width");
_Static_assert(sizeof(intptr_t) == sizeof(void *), "intptr_t vs pointer");
_Static_assert(sizeof(uintptr_t) == sizeof(void *), "uintptr_t vs pointer");
_Static_assert(sizeof(intmax_t) == 8, "intmax_t is not 8 bytes");
_Static_assert(sizeof(uintmax_t) == 8, "uintmax_t is not 8 bytes");

_Static_assert(INT8_MAX == 127, "INT8_MAX");
_Static_assert(INT16_MAX == 32767, "INT16_MAX");
_Static_assert(INT32_MAX == 2147483647, "INT32_MAX");
_Static_assert(INT64_MAX == 9223372036854775807LL, "INT64_MAX");
_Static_assert(UINT8_MAX == 255U, "UINT8_MAX");
_Static_assert(UINT16_MAX == 65535U, "UINT16_MAX");
_Static_assert(UINT32_MAX == 4294967295U, "UINT32_MAX");
_Static_assert(UINT64_MAX == 18446744073709551615ULL, "UINT64_MAX");
_Static_assert(INT8_MIN == -127 - 1, "INT8_MIN");
_Static_assert(INT32_MIN == -2147483647 - 1, "INT32_MIN");
_Static_assert(INT64_MIN == -9223372036854775807LL - 1, "INT64_MIN");

_Static_assert(INT32_MAX == __INT32_MAX__, "INT32_MAX vs builtin");
_Static_assert(UINT64_MAX == __UINT64_MAX__, "UINT64_MAX vs builtin");
_Static_assert(INTPTR_MAX == __INTPTR_MAX__, "INTPTR_MAX vs builtin");
_Static_assert(UINTPTR_MAX == __UINTPTR_MAX__, "UINTPTR_MAX vs builtin");
_Static_assert(INTMAX_MAX == __INTMAX_MAX__, "INTMAX_MAX vs builtin");
_Static_assert(UINTMAX_MAX == __UINTMAX_MAX__, "UINTMAX_MAX vs builtin");
_Static_assert(SIZE_MAX == __SIZE_MAX__, "SIZE_MAX vs builtin");
_Static_assert(PTRDIFF_MAX == __PTRDIFF_MAX__, "PTRDIFF_MAX vs builtin");

int probe(void)
{
	return (int)sizeof(intptr_t);
}
EOF

# --- probe B source: include-order against the capture's headers ----------
make_order_probe() {
	local file=$1 first=$2 second=$3
	{
		echo "#include <${first}>"
		echo "#include <${second}>"
		cat <<'EOF'

_Static_assert(sizeof(int64_t) == 8, "int64_t width");
_Static_assert(__builtin_types_compatible_p(int64_t, __int64_t),
	       "int64_t is not the native __int64_t");
_Static_assert(__builtin_types_compatible_p(intmax_t, __int64_t),
	       "intmax_t is not the native __int64_t");
_Static_assert(__builtin_types_compatible_p(intptr_t, long int),
	       "intptr_t is not the native long int");
_Static_assert(__builtin_types_compatible_p(uintptr_t, unsigned long int),
	       "uintptr_t is not the native unsigned long int");
_Static_assert(INT32_MAX == 2147483647, "INT32_MAX");
_Static_assert(UINT64_MAX == 18446744073709551615ULL, "UINT64_MAX");

int probe(void)
{
	return (int)sizeof(intptr_t);
}
EOF
	} > "$file"
}

make_order_probe "${tmpdir}/order-stdint-first.c" stdint.h inttypes.h
make_order_probe "${tmpdir}/order-inttypes-first.c" inttypes.h stdint.h

# --- probe C source: C++ consumers ----------------------------------------
cat > "${tmpdir}/order-stdint-first.cc" <<'EOF'
#include <stdint.h>
#include <inttypes.h>

#ifndef EXPECT_PTR_SIZE
#error "the harness must define EXPECT_PTR_SIZE"
#endif

static_assert(sizeof(int64_t) == 8, "int64_t width");
static_assert(sizeof(uint64_t) == 8, "uint64_t width");
static_assert(sizeof(intmax_t) == 8, "intmax_t width");
static_assert(sizeof(uintmax_t) == 8, "uintmax_t width");
static_assert(sizeof(intptr_t) == EXPECT_PTR_SIZE, "intptr_t width");
static_assert(sizeof(uintptr_t) == sizeof(void *), "uintptr_t vs pointer");
static_assert(INT32_MAX == 2147483647, "INT32_MAX");
static_assert(UINT64_MAX == 18446744073709551615ULL, "UINT64_MAX");

int probe();
EOF

cat > "${tmpdir}/order-inttypes-first.cc" <<'EOF'
#include <inttypes.h>
#include <stdint.h>

#ifndef EXPECT_PTR_SIZE
#error "the harness must define EXPECT_PTR_SIZE"
#endif

static_assert(sizeof(int64_t) == 8, "int64_t width");
static_assert(sizeof(uint64_t) == 8, "uint64_t width");
static_assert(sizeof(intmax_t) == 8, "intmax_t width");
static_assert(sizeof(uintmax_t) == 8, "uintmax_t width");
static_assert(sizeof(intptr_t) == EXPECT_PTR_SIZE, "intptr_t width");
static_assert(sizeof(uintptr_t) == sizeof(void *), "uintptr_t vs pointer");
static_assert(INT32_MAX == 2147483647, "INT32_MAX");
static_assert(UINT64_MAX == 18446744073709551615ULL, "UINT64_MAX");

int probe();
EOF

# --- probe A: hosted <stdint.h> -------------------------------------------
for abi in 32 n32; do
	check "A hosted <stdint.h> -mabi=${abi}" \
		"$CC" "${sysroot_arg[@]}" -mabi="$abi" \
		-DEXPECT_PTR_SIZE="$PTR_SIZE" -Wall -Wextra -Werror \
		-c "${tmpdir}/stdint.c" -o "${tmpdir}/stdint-${abi}.o"
done

# --- probe B: include order with the capture's <inttypes.h> ---------------
if [ "$have_native_headers" -eq 1 ]; then
	for abi in 32 n32; do
		for src in order-stdint-first order-inttypes-first; do
			check "B ${src} -mabi=${abi}" \
				"$CC" "${sysroot_arg[@]}" -mabi="$abi" \
				-Wall -Wextra -Werror \
				-c "${tmpdir}/${src}.c" -o "${tmpdir}/${src}-${abi}.o"
		done
	done
else
	skip "B include-order probes: no sysroot inttypes.h (${SYSROOT:-none})"
fi

# --- probe C: C++ consumer ------------------------------------------------
cc1plus=$("$CC" -print-prog-name=cc1plus 2>/dev/null || true)
case "$cc1plus" in
	*/*) ;;
	*) cc1plus= ;;
esac

if [ -z "$cc1plus" ] || [ ! -x "$cc1plus" ]; then
	skip "C C++ probes: cross has no cc1plus (C-only build)"
elif [ "$have_native_headers" -ne 1 ]; then
	skip "C C++ probes: no sysroot inttypes.h (${SYSROOT:-none})"
else
	for abi in 32 n32; do
		for src in order-stdint-first order-inttypes-first; do
			check "C ${src} -mabi=${abi}" \
				"$CC" "${sysroot_arg[@]}" -mabi="$abi" -x c++ \
				-DEXPECT_PTR_SIZE="$PTR_SIZE" -Wall -Wextra -Werror \
				-c "${tmpdir}/${src}.cc" -o "${tmpdir}/${src}-${abi}.o"
		done
	done
fi

# --- probe D: freestanding <stdint.h> -------------------------------------
for abi in 32 n32; do
	check "D freestanding <stdint.h> -mabi=${abi}" \
		"$CC" "${sysroot_arg[@]}" -mabi="$abi" -ffreestanding \
		-DEXPECT_PTR_SIZE="$PTR_SIZE" -Wall -Wextra -Werror \
		-c "${tmpdir}/stdint.c" -o "${tmpdir}/stdint-freestanding-${abi}.o"
done

if [ "$failures" -ne 0 ]; then
	echo "test-hosted-stdint: ${failures} probe(s) failed" >&2
	exit 1
fi

echo "all probes passed"
