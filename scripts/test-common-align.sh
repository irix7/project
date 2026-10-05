#!/usr/bin/env bash
#
# Regression test: common-symbol alignment survives codegen and linking
# (issue #28).
#
# The IRIX patch series once changed mips_declare_common_object()'s final
# argument to false. That argument selects the three-operand
# `.comm name,size,align` directive; it does not control symbol locality.
# With -fcommon an over-aligned tentative definition then emitted
# `.comm name,size`, so the assembler recorded a smaller alignment than
# the optimiser assumed, and the final link could place the symbol at an
# address the compiler had already folded away.
#
# This script compiles synthetic ordinary and 64-byte-aligned tentative
# definitions for o32 and n32, with -fcommon and -fno-common, and checks:
#
#   * the object records the requested alignment (the .comm operand, or
#     the .bss section alignment and symbol offset);
#   * the final linked symbol address is a multiple of that alignment;
#   * the compiler's optimised alignment assumption (a probe folded to
#     "always aligned") agrees with the linked address.
#
# It is guest-free: the produced binaries are never executed, the ELFs
# are inspected with the toolchain's own readelf. Run it against a
# rebuilt prefix:
#
#   scripts/test-common-align.sh --prefix <rebuilt-prefix>
#
# The default prefix is $IRIX_PREFIX, then $PWD/.scratch/toolchain-16.2/prefix;
# when neither exists the script skips instead of failing.
#
# Usage: scripts/test-common-align.sh [--prefix DIR]
#
set -euo pipefail

TARGET=mips-sgi-irix6.5
PREFIX=
PREFIX_SET=0

usage() {
	cat <<'EOF'
Usage: scripts/test-common-align.sh [--prefix DIR]

  --prefix DIR    installation prefix of the cross toolchain
                  (default: $IRIX_PREFIX, then .scratch/toolchain-16.2/prefix)
  -h, --help      show this help
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
		--prefix) PREFIX=$2; PREFIX_SET=1; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

[ -n "$PREFIX" ] || PREFIX=${IRIX_PREFIX:-$PWD/.scratch/toolchain-16.2/prefix}

if [ ! -d "$PREFIX" ]; then
	if [ "$PREFIX_SET" -eq 1 ]; then
		die "toolchain prefix not found: $PREFIX"
	fi
	echo "skip: toolchain prefix not found: $PREFIX"
	exit 0
fi
PREFIX=$(cd "$PREFIX" && pwd)

CC="${PREFIX}/bin/${TARGET}-gcc"
AS="${PREFIX}/bin/${TARGET}-as"
LD="${PREFIX}/bin/${TARGET}-ld"
READELF="${PREFIX}/bin/${TARGET}-readelf"

for tool in "$CC" "$AS" "$LD" "$READELF"; do
	[ -x "$tool" ] || die "missing tool: $tool"
done

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

cat > "${tmpdir}/defs.c" <<'EOF'
/* Synthetic public input for the common-alignment regression (issue #28):
   one ordinary and one 64-byte-aligned tentative definition. */
int ordinary_slot[8];
__attribute__((aligned(64))) char aligned_slot[64];
EOF

cat > "${tmpdir}/probe.c" <<'EOF'
/* The functions fold to a constant when the compiler's assumed alignment
   holds, and compute the mask otherwise. */
extern int ordinary_slot[];
extern char aligned_slot[] __attribute__((aligned(64)));

int ordinary_slot_is_aligned(void)
{
	return ((unsigned long) ordinary_slot & 3) == 0;
}

int aligned_slot_is_aligned(void)
{
	return ((unsigned long) aligned_slot & 63) == 0;
}
EOF

# sym_field FILE SYMBOL FIELD: print one field of the symbol's table line.
sym_field() {
	local value
	value=$("$READELF" -s "$1" | awk -v sym="$2" -v field="$3" \
		'$NF == sym { print $field; exit }')
	[ -n "$value" ] || die "symbol ${2} not found in $1"
	echo "$value"
}

# sym_value FILE SYMBOL: print the symbol's st_value in decimal.
sym_value() {
	echo $((0x$(sym_field "$1" "$2" 2)))
}

# bss_alignment FILE: print the .bss section's alignment in bytes.
bss_alignment() {
	local align
	align=$("$READELF" -S "$1" | awk '$3 == ".bss" { print $NF; exit }')
	[ -n "$align" ] || die "no .bss section in $1"
	echo "$align"
}

# function_body FILE FUNCTION: print FUNCTION's assembly body.
function_body() {
	awk -v fn="$2" '
		$0 == fn ":" { inside = 1 }
		inside && /^[[:space:]]*\.end[[:space:]]/ { inside = 0 }
		inside { print }
	' "$1"
}

# check_assumption FILE LABEL: require both alignment probes to have folded
# to a constant "aligned" result, matching the compiler's assumption.
check_assumption() {
	local fn
	for fn in ordinary_slot_is_aligned aligned_slot_is_aligned; do
		function_body "$1" "$fn" | grep -qE 'li[[:space:]]+\$2,1([[:space:]]|$)' ||
			die "${2}: ${fn} did not fold to the assumed alignment"
		if function_body "$1" "$fn" | grep -qE 'andi[[:space:]]+\$2,\$2,0x'; then
			die "${2}: ${fn} still computes its alignment check"
		fi
	done
}

for abi in 32 n32; do
	case "$abi" in
		32) abiname=o32; ordinary_align=4 ;;
		n32) abiname=n32; ordinary_align=8 ;;
	esac
	for common in fcommon fno-common; do
		label="${abiname} -${common}"
		tag="abi${abi}-${common}"
		obj="${tmpdir}/defs-${tag}.o"
		probe_obj="${tmpdir}/probe-${tag}.o"
		asm="${tmpdir}/defs-${tag}.s"
		probe_asm="${tmpdir}/probe-${tag}.s"
		elf="${tmpdir}/${tag}.elf"

		"$CC" -mabi="$abi" -O2 -"$common" -S "${tmpdir}/defs.c" -o "$asm"
		"$CC" -mabi="$abi" -O2 -"$common" -S "${tmpdir}/probe.c" -o "$probe_asm"
		"$CC" -mabi="$abi" -O2 -"$common" -c "${tmpdir}/defs.c" -o "$obj"
		"$CC" -mabi="$abi" -O2 -"$common" -c "${tmpdir}/probe.c" -o "$probe_obj"
		"$CC" -mabi="$abi" -nostdlib -nostartfiles -Wl,-e,0 \
			"$obj" "$probe_obj" -o "$elf"

		if [ "$common" = fcommon ]; then
			grep -qE '^[[:space:]]*\.comm[[:space:]]+aligned_slot,64,64[[:space:]]*$' "$asm" ||
				die "${label}: .comm has no 64-byte alignment operand"
			grep -qE "^[[:space:]]*\.comm[[:space:]]+ordinary_slot,32,${ordinary_align}[[:space:]]*$" "$asm" ||
				die "${label}: .comm has no ${ordinary_align}-byte alignment operand"
			[ "$(sym_field "$obj" aligned_slot 7)" = COM ] ||
				die "${label}: aligned_slot is not a common symbol"
			aligned_obj_align=$(sym_value "$obj" aligned_slot)
			[ "$aligned_obj_align" -eq 64 ] ||
				die "${label}: object records alignment ${aligned_obj_align}, expected 64"
			ordinary_obj_align=$(sym_value "$obj" ordinary_slot)
			[ "$ordinary_obj_align" -eq "$ordinary_align" ] ||
				die "${label}: object records alignment ${ordinary_obj_align}, expected ${ordinary_align}"
		else
			aligned_obj_align=$(bss_alignment "$obj")
			[ "$aligned_obj_align" -eq 64 ] ||
				die "${label}: .bss alignment is ${aligned_obj_align}, expected 64"
			aligned_off=$(sym_value "$obj" aligned_slot)
			[ $((aligned_off % 64)) -eq 0 ] ||
				die "${label}: aligned_slot offset ${aligned_off} is not 64-aligned"
			ordinary_off=$(sym_value "$obj" ordinary_slot)
			[ $((ordinary_off % ordinary_align)) -eq 0 ] ||
				die "${label}: ordinary_slot offset ${ordinary_off} is not ${ordinary_align}-aligned"
		fi

		aligned_addr=$(sym_value "$elf" aligned_slot)
		ordinary_addr=$(sym_value "$elf" ordinary_slot)
		[ $((aligned_addr % 64)) -eq 0 ] ||
			die "${label}: linked aligned_slot at 0x$(printf '%x' "$aligned_addr") is not 64-aligned"
		[ $((ordinary_addr % ordinary_align)) -eq 0 ] ||
			die "${label}: linked ordinary_slot at 0x$(printf '%x' "$ordinary_addr") is not ${ordinary_align}-aligned"

		check_assumption "$probe_asm" "$label"
		pass "${label}: requested alignment survives .comm/.bss, linking and the compiler assumption"
	done
done

echo "all common-alignment checks passed"
