#!/usr/bin/env bash
#
# Verify an installed IRIX 6.5 cross toolchain.
#
# Compiles a trivial translation unit for each in-scope IRIX ABI and checks
# the ELF headers with the toolchain's own readelf and objdump: big-endian
# MIPS, ELF32 with the MIPS II flags for o32, and ELF32 with EF_MIPS_ABI2 and
# MIPS III for n32. The default ABI (no -mabi flag) must be o32, matching the
# IRIX 6.5 environment. n64 is emitted by the multilib but outside the
# project's scope: the IP22 cannot execute it (ADR-0003 scope note).
#
# The compiler-version assertion is parameterised so the same script checks
# the 16.2 series and the 15.x fallback builds; every ELF check is identical
# for all of them.
#
# Usage: scripts/verify-toolchain.sh --prefix DIR [--gcc-version VERSION]
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
PREFIX=
GCC_VERSION=16.2.0

usage() {
	cat <<'EOF'
Usage: scripts/verify-toolchain.sh --prefix DIR [--gcc-version VERSION]

  --prefix DIR         installation prefix of the cross toolchain
  --gcc-version VER    expected GCC release (default 16.2.0)
  -h, --help           show this help
EOF
}

die() {
	echo "FAIL: $*" >&2
	exit 1
}

pass() {
	echo "ok: $*"
}

while [ $# -gt 0 ]; do
	case "$1" in
		--prefix) PREFIX=$2; shift 2 ;;
		--gcc-version) GCC_VERSION=$2; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

[ -n "$PREFIX" ] || die "--prefix is required"
[ -d "$PREFIX" ] || die "prefix not found: $PREFIX"

CC="${PREFIX}/bin/${TARGET}-gcc"
AS="${PREFIX}/bin/${TARGET}-as"
LD="${PREFIX}/bin/${TARGET}-ld"
READELF="${PREFIX}/bin/${TARGET}-readelf"
OBJDUMP="${PREFIX}/bin/${TARGET}-objdump"

for tool in "$CC" "$AS" "$LD" "$READELF" "$OBJDUMP"; do
	[ -x "$tool" ] || die "missing tool: $tool"
done

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

cat > "${tmpdir}/probe.c" <<'EOF'
int add(int a, int b)
{
	return a + b;
}
EOF

# elf_header_fields OBJECT; sets class/endian/machine/flags.
elf_header_fields() {
	local hdr
	hdr=$("$READELF" -h "$1")
	class=$(grep -E '^  Class:' <<<"$hdr" | awk '{print $NF}')
	endian=$(grep -E '^  Data:' <<<"$hdr")
	machine=$(grep -E '^  Machine:' <<<"$hdr")
	flags_hex=$(sed -n 's/^  Flags:[[:space:]]*\(0x[0-9a-fA-F]*\).*/\1/p' <<<"$hdr")
	[ -n "$flags_hex" ] || die "could not parse ELF flags of $1"
	flags=$((flags_hex))
}

# check_common ABINAME OBJECT
check_common() {
	[ -n "$class" ] || die "$1: no ELF class in header"
	grep -q 'big endian' <<<"$endian" || die "$1: not big-endian"
	grep -q 'MIPS' <<<"$machine" || die "$1: not a MIPS object"
	"$OBJDUMP" -f "$2" | grep -q 'mips' || die "$1: objdump cannot read the object"
	"$OBJDUMP" -d "$2" >/dev/null || die "$1: objdump cannot disassemble"
}

compile() {
	local out=$1
	shift
	"$CC" -c "$@" "${tmpdir}/probe.c" -o "$out"
}

# --- toolchain identity ----------------------------------------------------
"$CC" -dumpmachine | grep -qx "$TARGET" ||
	die "gcc -dumpmachine is not ${TARGET}"
"$CC" --version | grep -qF "$GCC_VERSION" ||
	die "gcc is not ${GCC_VERSION}"
"$AS" --version | grep -q 'GNU assembler' || die "as is not GNU as"
"$LD" --version | grep -q 'GNU ld' || die "ld is not GNU ld"
pass "toolchain identifies as ${TARGET}, GCC ${GCC_VERSION} with GNU as/ld"

EF_MIPS_ABI2=0x20
EF_MIPS_ARCH=0xf0000000
EF_MIPS_ARCH_2=0x10000000
EF_MIPS_ARCH_3=0x20000000

# --- o32 -------------------------------------------------------------------
compile "${tmpdir}/o32.o" -mabi=32
elf_header_fields "${tmpdir}/o32.o"
check_common o32 "${tmpdir}/o32.o"
[ "$class" = ELF32 ] || die "o32: expected ELF32, got ${class}"
if (( flags & EF_MIPS_ABI2 )); then
	die "o32: unexpected EF_MIPS_ABI2 flag"
fi
if (( (flags & EF_MIPS_ARCH) != EF_MIPS_ARCH_2 )); then
	die "o32: expected the MIPS II ISA (flags ${flags_hex})"
fi
pass "o32: ELF32 big-endian MIPS II (-mabi=32)"

# --- default ABI -----------------------------------------------------------
compile "${tmpdir}/default.o"
elf_header_fields "${tmpdir}/default.o"
check_common default "${tmpdir}/default.o"
[ "$class" = ELF32 ] || die "default: expected ELF32, got ${class}"
if (( (flags & EF_MIPS_ARCH) != EF_MIPS_ARCH_2 )); then
	die "default ABI is not o32 (flags ${flags_hex})"
fi
pass "default (no -mabi): o32, matching the IRIX 6.5 environment"

# --- n32 -------------------------------------------------------------------
compile "${tmpdir}/n32.o" -mabi=n32
elf_header_fields "${tmpdir}/n32.o"
check_common n32 "${tmpdir}/n32.o"
[ "$class" = ELF32 ] || die "n32: expected ELF32, got ${class}"
(( flags & EF_MIPS_ABI2 )) || die "n32: EF_MIPS_ABI2 flag missing (flags ${flags_hex})"
if (( (flags & EF_MIPS_ARCH) != EF_MIPS_ARCH_3 )); then
	die "n32: expected the MIPS III ISA (flags ${flags_hex})"
fi
pass "n32: ELF32 big-endian MIPS III with EF_MIPS_ABI2 (-mabi=n32)"

echo "all checks passed"
