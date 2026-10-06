#!/usr/bin/env bash
#
# Guest-free regression for a vanilla GNU binutils release driving the IRIX
# cross (issue #21). It proves the o32 and n32 emission and link paths work
# when the cross GCC's assembler and linker are the candidate binutils
# build, and it inspects the resulting ELF headers, relocations, startfiles
# and dynamic dependencies.
#
# Selector mechanics: a cross built by scripts/build-toolchain.sh bakes the
# absolute --with-as/--with-ld paths into the driver, and both the driver's
# find_a_program and collect2 prefer those compiled-in defaults, so -B
# cannot redirect as/ld to a separate binutils prefix. This script instead
# generates a user specs file from `gcc -dumpspecs` that points *invoke_as
# and *linker at the candidate tools; -v traces then prove the driver really
# invoked them. The guest smoke should either use a prefix whose GCC was
# configured against the selected binutils (build-toolchain.sh --binutils)
# or pass the generated specs file through --cflags.
#
# Probes, all host-side and reading the captured sysroot in place:
#   1. identity. The candidate as/ld identify as GNU tools of the requested
#      major version and support the mips-sgi-irix6.5 o32/n32/n64 emulations.
#   2. emission. A header-free probe compiles for o32 and n32 with the
#      candidate as; ELF class/endianness/ISA flags and the canonical MIPS
#      relocation types must match the ABI.
#   3. link. oracle/hello.c links for o32 and n32 with the candidate ld
#      against the capture; the driver trace must name the candidate ld and
#      the IRIX startfiles (crt1.o, irix-crti.o, crtbegin.o, crtend.o,
#      irix-crtn.o), and the binary must be dynamic with the expected
#      interpreter and libc/libm dependencies.
#
# Emission runs without a sysroot; the link phase skips cleanly when the
# configured sysroot is absent, and the whole script skips cleanly when the
# prefixes are absent. No guest, no rig and no SGI artefact is required.
#
# Usage: scripts/test-binutils-vanilla.sh [options]
#
#   --gcc-prefix DIR        cross prefix (default $IRIX_PREFIX, then
#                           <repo>/.scratch/toolchain-16.2.0/prefix)
#   --binutils-prefix DIR   candidate as/ld/readelf prefix (default: the
#                           GCC prefix, as build-toolchain.sh installs both
#                           into one prefix)
#   --binutils-version VER  expected binutils version (default 2.47)
#   --sysroot DIR           sysroot the cross links against (default
#                           $CC -print-sysroot)
#   --write-specs FILE      keep the generated specs file at FILE, so the
#                           guest smoke can select a separate binutils
#                           prefix with --cflags "-specs=FILE -lm"
#   --check-irix-crt1-alignment
#                           require the linked .init and __istart alignment
#   -h, --help              show this help
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

GCC_PREFIX=${IRIX_PREFIX:-${REPO_ROOT}/.scratch/toolchain-16.2.0/prefix}
BINUTILS_PREFIX=
BINUTILS_VERSION=2.47
SYSROOT=
SPECS_OUT=
CHECK_IRIX_CRT1_ALIGNMENT=0

usage() {
	cat <<'EOF'
Usage: scripts/test-binutils-vanilla.sh [options]

Check that a candidate vanilla GNU binutils release drives the IRIX cross
for o32 and n32: identity, emission (ELF headers and relocations) and link
(startfiles, interpreter and dependencies). See the script header.

  --gcc-prefix DIR        cross prefix (default $IRIX_PREFIX, then
                          <repo>/.scratch/toolchain-16.2.0/prefix)
  --binutils-prefix DIR   candidate as/ld/readelf prefix (default: the
                          GCC prefix)
  --binutils-version VER  expected binutils version (default 2.47)
  --sysroot DIR           sysroot the cross links against (default
                          $CC -print-sysroot)
  --write-specs FILE      keep the generated specs file at FILE for the
                          guest smoke (--cflags "-specs=FILE -lm")
  --check-irix-crt1-alignment
                          require the linked .init and __istart alignment
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
		--gcc-prefix) GCC_PREFIX=$2; shift 2 ;;
		--binutils-prefix) BINUTILS_PREFIX=$2; shift 2 ;;
		--binutils-version) BINUTILS_VERSION=$2; shift 2 ;;
		--sysroot) SYSROOT=$2; shift 2 ;;
		--write-specs) SPECS_OUT=$2; shift 2 ;;
		--check-irix-crt1-alignment) CHECK_IRIX_CRT1_ALIGNMENT=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

# Skip rather than fail: the script is cheap enough to run unconditionally
# from a checkout that has not built the cross or the candidate binutils.
[ -d "$GCC_PREFIX" ] ||
	{
		skip "cross prefix not found: $GCC_PREFIX (build it with scripts/build-toolchain.sh, or pass --gcc-prefix)"
		exit 0
	}
if [ -z "$BINUTILS_PREFIX" ]; then
	BINUTILS_PREFIX=$GCC_PREFIX
fi
[ -d "$BINUTILS_PREFIX" ] ||
	{
		skip "binutils prefix not found: $BINUTILS_PREFIX (pass --binutils-prefix)"
		exit 0
	}
GCC_PREFIX=$(cd "$GCC_PREFIX" && pwd)
BINUTILS_PREFIX=$(cd "$BINUTILS_PREFIX" && pwd)

CC="${GCC_PREFIX}/bin/${TARGET}-gcc"
AS="${BINUTILS_PREFIX}/bin/${TARGET}-as"
LD="${BINUTILS_PREFIX}/bin/${TARGET}-ld"
READELF="${BINUTILS_PREFIX}/bin/${TARGET}-readelf"
OBJDUMP="${BINUTILS_PREFIX}/bin/${TARGET}-objdump"
[ -x "$CC" ] || die "cross compiler not found: $CC"
for tool in "$AS" "$LD" "$READELF" "$OBJDUMP"; do
	[ -x "$tool" ] || die "binutils tool not found: $tool"
done

# --- probe 1: identity -----------------------------------------------------
"$AS" --version | grep -q 'GNU assembler' || die "as is not GNU as"
"$LD" --version | grep -q 'GNU ld' || die "ld is not GNU ld"
"$AS" --version | grep -qF "$BINUTILS_VERSION" ||
	die "as does not identify as binutils ${BINUTILS_VERSION}: $("$AS" --version | head -n 1)"
"$LD" --version | grep -qF "$BINUTILS_VERSION" ||
	die "ld does not identify as binutils ${BINUTILS_VERSION}: $("$LD" --version | head -n 1)"
for emu in elf32bsmip elf32bmipn32 elf64bmip; do
	"$LD" -V 2>/dev/null | grep -qw "$emu" ||
		die "ld does not support the ${emu} emulation"
done
pass "as/ld are GNU binutils ${BINUTILS_VERSION} with the IRIX o32/n32/n64 emulations"

if [ -z "$SYSROOT" ]; then
	SYSROOT=$("$CC" -print-sysroot) ||
		die "cannot query the configured sysroot ($CC -print-sysroot)"
fi
[ -n "$SYSROOT" ] || SYSROOT=/nonexistent

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# --- specs override: point the driver at the candidate as/ld --------------
# A cross configured with --with-as/--with-ld prefers those absolute paths
# over -B, in find_a_program and in collect2, so the only way to drive the
# candidate tools from an already-built driver is a user specs file. The
# substitutions are asserted below; an upstream spec reflow that breaks them
# fails loudly instead of testing the wrong tools.
SPECS="$tmpdir/binutils.specs"
"$CC" -dumpspecs | awk -v as="$AS" -v ld="$LD" '
	/^\*invoke_as:/ { in_as = 1 }
	in_as && /^ as / {
		print " " as " " substr($0, 5)
		in_as = 0
		next
	}
	/^\*linker:/ { in_ld = 1; print; next }
	in_ld && $0 == "collect2" { print ld; in_ld = 0; next }
	{ print }
' >"$SPECS"
grep -qF "$AS" "$SPECS" || die "could not read the *invoke_as spec to override as"
grep -qF "$LD" "$SPECS" || die "could not find the *linker spec to override ld"
if [ -n "$SPECS_OUT" ]; then
	cp "$SPECS" "$SPECS_OUT" || die "cannot write specs file: $SPECS_OUT"
	pass "wrote the candidate-tool specs file to $SPECS_OUT"
fi

printf 'extern int external_counter;\n' >"$tmpdir/reloc.c"
cat >>"$tmpdir/reloc.c" <<'EOF'
extern int external_add(int a, int b);
int local_data = 7;
int probe(void) { return external_counter + external_add(local_data, 1); }
EOF

EF_MIPS_ABI2=0x20
EF_MIPS_ARCH=0xf0000000
EF_MIPS_ARCH_2=0x10000000
EF_MIPS_ARCH_3=0x20000000

# elf_header_fields OBJECT; sets class/endian/machine/flags from the
# candidate binutils readelf.
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

check_common() {
	[ -n "$class" ] || die "$1: no ELF class in header"
	grep -q 'big endian' <<<"$endian" || die "$1: not big-endian"
	grep -q 'MIPS' <<<"$machine" || die "$1: not a MIPS object"
	"$OBJDUMP" -f "$2" | grep -q 'mips' || die "$1: objdump cannot read the object"
}

# require_reloc OBJECT TYPE: the readelf relocation dump must name TYPE.
require_reloc() {
	"$READELF" -r "$1" | grep -qw "$2" ||
		die "$3: relocation table does not name $2"
}

compile_trace() {
	local abi=$1 mabi=$2 out=$3 trace=$4
	"$CC" -v -specs="$SPECS" -mabi="$mabi" -O2 -c "$tmpdir/reloc.c" -o "$out" \
		>"$trace" 2>&1 || die "$abi: candidate as failed to assemble the probe"
	grep -qF "$AS" "$trace" ||
		die "$abi: driver trace does not invoke the candidate as ($AS)"
}

# --- probe 2: o32/n32 emission --------------------------------------------
compile_trace o32 32 "$tmpdir/reloc-32.o" "$tmpdir/trace-assemble-32"
elf_header_fields "$tmpdir/reloc-32.o"
check_common o32 "$tmpdir/reloc-32.o"
[ "$class" = ELF32 ] || die "o32: expected ELF32, got ${class}"
if (( flags & EF_MIPS_ABI2 )); then
	die "o32: unexpected EF_MIPS_ABI2 flag"
fi
if (( (flags & EF_MIPS_ARCH) != EF_MIPS_ARCH_2 )); then
	die "o32: expected the MIPS II ISA (flags ${flags_hex})"
fi
for reloc in R_MIPS_CALL16 R_MIPS_GOT16 R_MIPS_HI16 R_MIPS_LO16; do
	require_reloc "$tmpdir/reloc-32.o" "$reloc" o32
done
pass "o32: candidate as emits ELF32 big-endian MIPS II with GOT/HI16/LO16 relocations"

compile_trace n32 n32 "$tmpdir/reloc-n32.o" "$tmpdir/trace-assemble-n32"
elf_header_fields "$tmpdir/reloc-n32.o"
check_common n32 "$tmpdir/reloc-n32.o"
[ "$class" = ELF32 ] || die "n32: expected ELF32, got ${class}"
(( flags & EF_MIPS_ABI2 )) || die "n32: EF_MIPS_ABI2 flag missing (flags ${flags_hex})"
if (( (flags & EF_MIPS_ARCH) != EF_MIPS_ARCH_3 )); then
	die "n32: expected the MIPS III ISA (flags ${flags_hex})"
fi
for reloc in R_MIPS_CALL16 R_MIPS_GOT_DISP R_MIPS_GPREL16; do
	require_reloc "$tmpdir/reloc-n32.o" "$reloc" n32
done
pass "n32: candidate as emits ELF32 big-endian MIPS III with GOT_DISP/GPREL16 relocations"

# --- probe 3: o32/n32 link ------------------------------------------------
if [ ! -d "$SYSROOT/usr/include" ]; then
	skip "configured sysroot has no headers ($SYSROOT); emission checks only"
	echo "all binutils emission checks passed"
	exit 0
fi

HELLO="${REPO_ROOT}/oracle/hello.c"
[ -f "$HELLO" ] || die "oracle/hello.c not found: $HELLO"

# require_name TRACE NAME: some token's last path component is NAME.
require_name() {
	local trace=$1 name=$2 tok
	while IFS= read -r tok; do
		tok=${tok%\"}
		tok=${tok#\"}
		case "$tok" in
			"$name"|*/"$name") return 0 ;;
		esac
	done < <(tr ' ' '\n' <"$trace")
	die "$3: driver trace does not name $name"
}

link_probe() {
	local abi=$1 mabi=$2 interp=$3
	local bin="$tmpdir/hello-$abi" trace="$tmpdir/trace-link-$abi"
	"$CC" -v -specs="$SPECS" --sysroot="$SYSROOT" -mabi="$mabi" \
		"$HELLO" -o "$bin" -lm >"$trace" 2>&1 ||
		die "$abi: candidate ld failed to link oracle/hello.c"
	grep -qF "$LD" "$trace" ||
		die "$abi: driver trace does not invoke the candidate ld ($LD)"
	for name in crt1.o irix-crti.o crtbegin.o crtend.o irix-crtn.o; do
		require_name "$trace" "$name" "$abi"
	done
	elf_header_fields "$bin"
	check_common "$abi" "$bin"
	"$READELF" -l "$bin" |
		grep -q "Requesting program interpreter: $interp" ||
		die "$abi: binary does not request the expected interpreter $interp"
	"$READELF" -d "$bin" | grep -q 'Shared library: \[libc\.so\.1\]' ||
		die "$abi: binary does not depend on libc.so.1"
	"$READELF" -d "$bin" | grep -q 'Shared library: \[libm\.so\]' ||
		die "$abi: binary does not depend on libm.so"
	pass "$abi: candidate ld links the IRIX startfiles and sysroot libs (interpreter $interp)"
	if (( CHECK_IRIX_CRT1_ALIGNMENT )); then
		local init_line init_addr init_align istart
		init_line=$("$READELF" -SW "$bin" | awk '$3 == ".init" { print; exit }')
		[ -n "$init_line" ] || die "$abi: linked image has no .init section"
		init_addr=$(awk '{ print $5 }' <<<"$init_line")
		init_align=$(awk '{ print $NF }' <<<"$init_line")
		istart=$("$READELF" -sW "$bin" | awk '$NF == "__istart" { print $2; exit }')
		[ -n "$istart" ] || die "$abi: linked image has no __istart symbol"
		case "$abi" in
			o32) (( init_align >= 16 )) || die "o32: .init alignment is $init_align, expected at least 16" ;;
			n32) (( init_align >= 4 )) || die "n32: .init alignment is $init_align, expected at least 4" ;;
		esac
		(( (init_align & (init_align - 1)) == 0 )) ||
			die "$abi: .init alignment $init_align is not a power of two"
		(( (16#$init_addr % init_align) == 0 )) ||
			die "$abi: .init address 0x$init_addr is not aligned to $init_align"
		(( (16#$istart % 4) == 0 )) ||
			die "$abi: __istart 0x$istart is not word-aligned"
		pass "$abi: .init alignment $init_align at 0x$init_addr; __istart 0x$istart is word-aligned"
	fi
}

link_probe o32 32 /usr/lib/libc.so.1
link_probe n32 n32 /usr/lib32/libc.so.1

echo "all binutils vanilla checks passed"
