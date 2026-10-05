#!/usr/bin/env bash
#
# Build the IRIX 6.5 cross toolchain.
#
# By default this builds the GCC 16.2 IRIX port: binutils 2.20.1 from the
# pdaxrom lineage plus the in-repo GCC 16.2 series (patches/gcc-16.2/),
# re-derived from pdaxrom/irix-gcc tag 1.2's 15.2 IRIX diff:
#
#   * binutils 2.20.1 with the pdaxrom IRIX patches
#   * GCC 16.2.0 with the in-repo IRIX series
#   * the local patches/ deltas that a sysroot-less host needs
#
# `--gcc 15.3.0` (or 15.2.0) selects the fallback recipe: the pdaxrom
# 15.2.0 IRIX diffs applied to that release plus the local patches. The
# 15.3.0 path is the heuristic fallback documented in docs/toolchain.md;
# the 15.2.0 path reproduces the original baseline tree.
#
# Unlike pdaxrom's own IRIX 6.5 configuration, which builds an n32-default
# compiler for the mips-sgi-irix6n32 triplet, this configures the canonical
# mips-sgi-irix6.5 target with o32 as the default ABI (the Indy's native
# environment, per ADR-0003). The n32-default patch is therefore not
# applied; -mabi=32/-mabi=n32/-mabi=64 still select all three multilibs.
#
# No SGI or licence-restricted material is downloaded or installed; a target
# sysroot is optional and supplied by the caller with IRIX_SYSROOT. With one,
# binutils is configured with the matching --with-sysroot so the cross's links
# resolve the capture's crt1.o, libc.so and libm.so (the smoke harness's
# dynamic-first model, ADR-0006); without one, the verified baseline is the
# sysroot-less C compiler below.
#
# Usage: scripts/build-toolchain.sh [options]
#
#   --work-dir DIR   scratch space for sources, builds and logs
#                    (default: <repo>/.scratch/toolchain-<gcc version>)
#   --prefix DIR     installation prefix (default: <work-dir>/prefix)
#   --jobs N         parallel make jobs (default: number of CPUs)
#   --sysroot DIR    target sysroot; enables libstdc++ if it is set
#   --languages L    GCC languages (default: c, or c,c++ with a sysroot)
#   --gcc VERSION    GCC release: 16.2.0 (default), 15.3.0 or 15.2.0
#   --clean          remove the work directory before building
#   -h, --help       show this help
#
# Environment: CC, CXX, GMP_PREFIX, MPFR_PREFIX, MPC_PREFIX, ISL_PREFIX and
# GCC_VERSION (overridden by --gcc) are honoured. The nix devshell sets the
# first five.
#
set -euo pipefail

TARGET=mips-sgi-irix6.5

BINUTILS_VERSION=2.20.1
BINUTILS_TARBALL="binutils-${BINUTILS_VERSION}.tar.bz2"
BINUTILS_SHA256=71d37c96451333c5c0b84b170169fdcb138bbb27397dc06281905d9717c8ed64

# GCC releases this script knows how to build. The sha256 values are
# pinned against the official GNU release checksums
# (https://gcc.gnu.org/pub/gcc/releases/gcc-<version>/sha512.sum).
GCC_VERSION=${GCC_VERSION:-16.2.0}
GCC_SHA256_16_2_0=e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e
GCC_SHA256_15_3_0=fa59c1beef8995f27c4d71c1df227587189315d3e6faff1bb4306e61b0c530eb
GCC_SHA256_15_2_0=438fd996826b0c82485a29da03a72d71d6e3541a83ec702df4271f6fe025d24e

# pdaxrom/irix-gcc tag 1.2 (main), the 15.2.0 IRIX port.
PDAXROM_COMMIT=2aa3421b4b4b9f8962cdd864243d82d7a458fcff
PDAXROM_RAW="https://raw.githubusercontent.com/pdaxrom/irix-gcc/${PDAXROM_COMMIT}/files"

# name:sha256 pairs, downloaded from PDAXROM_RAW.
BINUTILS_PATCHES=(
  "binutils-2.20.1-irix.diff:58ceeddf3ce3eda038a63f2b534d77bee540893619b67b06bfad095cef87ceee"
  "binutils-2.20.1-arm64-build-fix.diff:c932f55fce87bc8ac9735a3dc238c9bc614515c79f20a903c2a8b3b91398497f"
)
GCC_15X_PATCHES=(
  "gcc-15.2.0-irix.diff:e5a4af77312218ce7b878928d8840de564cae2d874c58bcb98ef21eacb1e99bb"
  "gcc-15.2.0-irix65-abi64.diff:38b0a8acee2dadc813527a88d3247892079e29e5f58a9c5e98837e032975c0f6"
  "gcc-15.2.0-irix65-stdc++.diff:93dd8a84bb9e2a987b7cce1a800e8aa7227eb202d481b5bef9afcf58af989d4b"
)

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

# The in-repo GCC 16.2 series; see patches/gcc-16.2/series and docs/toolchain.md.
GCC_SERIES_DIR=${REPO_ROOT}/patches/gcc-16.2

# Local deltas against the pdaxrom series, shipped in this repository. They
# are folded into the 16.2 series (which omits LIMITS_H_TEST altogether) and
# remain here for the 15.x fallback recipe.
LOCAL_GCC_PATCHES=(
  "${REPO_ROOT}/patches/0001-t-iris-conditional-limits-h.patch"
  "${REPO_ROOT}/patches/0002-t-iris6-conditional-limits-h.patch"
)

WORK_DIR=${WORK_DIR:-}
PREFIX=${PREFIX:-}
JOBS=${JOBS:-$( (nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1) )}
IRIX_SYSROOT=${IRIX_SYSROOT:-}
LANGUAGES=${LANGUAGES:-}
CLEAN=0

usage() {
	cat <<'EOF'
Build the IRIX 6.5 cross toolchain (binutils 2.20.1 + GCC 16.2.0 by
default) targeting mips-sgi-irix6.5 with big-endian o32, n32 and n64
multilibs. The gcc-16.2 IRIX series is in-repo; --gcc selects the 15.3.0
or 15.2.0 pdaxrom fallback recipe.

Usage: scripts/build-toolchain.sh [options]

  --work-dir DIR   scratch space for sources, builds and logs
                   (default: <repo>/.scratch/toolchain-<gcc version>)
  --prefix DIR     installation prefix (default: <work-dir>/prefix)
  --jobs N         parallel make jobs (default: number of CPUs)
  --sysroot DIR    target sysroot; enables libstdc++ if it is set
  --languages L    GCC languages (default: c, or c,c++ with a sysroot)
  --gcc VERSION    GCC release: 16.2.0 (default), 15.3.0 or 15.2.0
  --clean          remove the work directory before building
  -h, --help       show this help

Environment: CC, CXX, GMP_PREFIX, MPFR_PREFIX, MPC_PREFIX, ISL_PREFIX and
GCC_VERSION (overridden by --gcc) are honoured. The nix devshell sets the
first five.
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

note() {
	printf '\n==> %s\n' "$*"
}

while [ $# -gt 0 ]; do
	case "$1" in
		--work-dir) WORK_DIR=$2; shift 2 ;;
		--prefix) PREFIX=$2; shift 2 ;;
		--jobs) JOBS=$2; shift 2 ;;
		--sysroot) IRIX_SYSROOT=$2; shift 2 ;;
		--languages) LANGUAGES=$2; shift 2 ;;
		--gcc) GCC_VERSION=$2; shift 2 ;;
		--clean) CLEAN=1; shift ;;
		-h|--help) usage; exit 0 ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

# Resolve the GCC recipe: 16.2.0 is the in-repo series, 15.3.0 and 15.2.0
# use the pdaxrom 15.2.0 diffs.
case "$GCC_VERSION" in
	16.2.0)
		GCC_SHA256=$GCC_SHA256_16_2_0
		GCC_RECIPE=series
		;;
	15.3.0)
		GCC_SHA256=$GCC_SHA256_15_3_0
		GCC_RECIPE=pdaxrom
		;;
	15.2.0)
		GCC_SHA256=$GCC_SHA256_15_2_0
		GCC_RECIPE=pdaxrom
		;;
	*)
		die "unsupported GCC version: ${GCC_VERSION} (known: 16.2.0, 15.3.0, 15.2.0)"
		;;
esac
GCC_TARBALL="gcc-${GCC_VERSION}.tar.xz"
WORK_DIR=${WORK_DIR:-${REPO_ROOT}/.scratch/toolchain-${GCC_VERSION}}

if [ "$CLEAN" -eq 1 ]; then
	rm -rf "$WORK_DIR"
fi

mkdir -p "$WORK_DIR"
WORK_DIR=$(cd "$WORK_DIR" && pwd)
PREFIX=${PREFIX:-${WORK_DIR}/prefix}
mkdir -p "$PREFIX"
PREFIX=$(cd "$PREFIX" && pwd)

if [ -n "$IRIX_SYSROOT" ]; then
	[ -d "$IRIX_SYSROOT" ] || die "sysroot not found: $IRIX_SYSROOT"
	IRIX_SYSROOT=$(cd "$IRIX_SYSROOT" && pwd)
fi
[ -n "$LANGUAGES" ] || {
	if [ -n "$IRIX_SYSROOT" ]; then LANGUAGES=c,c++; else LANGUAGES=c; fi
}

DOWNLOADS=${WORK_DIR}/downloads
SRC_DIR=${WORK_DIR}/src
BUILD_DIR=${WORK_DIR}/build
LOGS=${WORK_DIR}/logs
STAMPS=${WORK_DIR}/stamps
mkdir -p "$DOWNLOADS" "$SRC_DIR" "$BUILD_DIR" "$LOGS" "$STAMPS"

for tool in curl tar patch make sha256sum; do
	command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done

# fetch URL DEST SHA256: verified download, skipped when DEST already matches.
fetch() {
	local url=$1 dest=$2 sha=$3
	if [ -f "$dest" ] && echo "${sha}  ${dest}" | sha256sum -c - >/dev/null 2>&1; then
		echo "    cached ${dest##*/}"
		return 0
	fi
	echo "    fetching ${dest##*/}"
	local part="${dest}.part"
	rm -f "$part"
	curl --fail --location --silent --show-error --retry 3 -o "$part" "$url"
	echo "${sha}  ${part}" | sha256sum -c - >/dev/null ||
		die "checksum mismatch for ${dest##*/}"
	mv "$part" "$dest"
}

# fetch_patches NAME:sha256 ... ; sets the global PATCH_FILES to local paths.
fetch_patches() {
	PATCH_FILES=()
	local entry name sha dest
	for entry in "$@"; do
		name=${entry%%:*}
		sha=${entry##*:}
		dest=${DOWNLOADS}/${name}
		fetch "${PDAXROM_RAW}/${name}" "$dest" "$sha"
		PATCH_FILES+=("$dest")
	done
}

# extract TARBALL DIRNAME
extract() {
	local tarball=$1 dir=$2
	if [ -d "${SRC_DIR}/${dir}" ]; then
		echo "    already extracted ${dir}"
		return 0
	fi
	echo "    extracting ${tarball##*/}"
	tar -xf "$tarball" -C "$SRC_DIR"
	[ -d "${SRC_DIR}/${dir}" ] || die "tarball did not contain ${dir}"
}

# apply_patches SRCDIR PATCH...
apply_patches() {
	local dir=$1; shift
	local marker_dir="${dir}/.irix-patched.d"
	mkdir -p "$marker_dir"
	local p name
	for p in "$@"; do
		name=$(basename "$p")
		if [ -f "${marker_dir}/${name}" ]; then
			echo "    already applied ${name}"
			continue
		fi
		echo "    applying ${name}"
		if patch -d "$dir" -p1 --batch --forward --silent < "$p"; then
			touch "${marker_dir}/${name}"
		elif patch -d "$dir" -p1 --batch -R --dry-run --silent < "$p"; then
			echo "    already applied ${name}"
			touch "${marker_dir}/${name}"
		else
			die "${name} did not apply cleanly"
		fi
	done
}

# apply_series SRCDIR SERIES-FILE: apply the patches named by the manifest,
# in order, via apply_patches.
apply_series() {
	local dir=$1 series=$2
	local p list=()
	while IFS= read -r p; do
		case "$p" in
			''|'#'*) continue ;;
		esac
		p="${series%/*}/${p}"
		[ -f "$p" ] || die "series entry not found: ${p}"
		list+=("$p")
	done < "$series"
	[ "${#list[@]}" -gt 0 ] || die "empty series: ${series}"
	apply_patches "$dir" "${list[@]}"
}

# configure_and_make SRCDIR BUILDDIR LOG CONFIGURE-ARGS...
#
# Configures out of tree on first use, and reconfigures from scratch when the
# arguments change, then makes and installs. Re-runs are cheap no-ops.
configure_and_make() {
	local src=$1 build=$2 log=$3
	shift 3
	local args=("$@")
	local args_file="${build}/.irix-configure-args"
	mkdir -p "$build"
	if [ -f "${build}/config.status" ] &&
		[ "$(cat "$args_file" 2>/dev/null || true)" != "${args[*]}" ]; then
		note "configure options changed; rebuilding ${build##*/}"
		rm -rf "$build"
		mkdir -p "$build"
	fi
	(
		cd "$build"
		if [ ! -f config.status ]; then
			"${src}/configure" "${args[@]}"
			printf '%s\n' "${args[*]}" > "$args_file"
		fi
		make -j"$JOBS" all
		make install
	) 2>&1 | tee -a "$log"
}

# ---------------------------------------------------------------- binutils --

build_binutils() {
	local args=(
		--prefix="$PREFIX"
		--target="$TARGET"
		--enable-multilib
		--disable-nls
		--disable-werror
		--disable-gdb
		--disable-sim
		--disable-gprof
		--disable-gold
	)
	# Without this, the installed ld rejects the --sysroot that a
	# sysroot-configured GCC passes it ("this linker was not configured to
	# use sysroots"), even though its --help advertises the option.
	if [ -n "$IRIX_SYSROOT" ]; then
		args+=("--with-sysroot=${IRIX_SYSROOT}")
	fi

	local stamp=${STAMPS}/binutils.installed
	if [ -f "$stamp" ] && [ -x "${PREFIX}/bin/${TARGET}-as" ] &&
		[ "$(cat "${stamp}.options" 2>/dev/null || true)" = "${args[*]}" ]; then
		note "binutils ${BINUTILS_VERSION} already installed"
		return 0
	fi

	note "binutils ${BINUTILS_VERSION}"
	fetch "https://ftp.gnu.org/gnu/binutils/${BINUTILS_TARBALL}" \
		"${DOWNLOADS}/${BINUTILS_TARBALL}" "$BINUTILS_SHA256"
	fetch_patches "${BINUTILS_PATCHES[@]}"
	extract "${DOWNLOADS}/${BINUTILS_TARBALL}" "binutils-${BINUTILS_VERSION}"
	local src="${SRC_DIR}/binutils-${BINUTILS_VERSION}"
	apply_patches "$src" "${PATCH_FILES[@]}"

	configure_and_make "$src" "${BUILD_DIR}/binutils" \
		"${LOGS}/binutils.log" "${args[@]}"

	[ -x "${PREFIX}/bin/${TARGET}-as" ] || die "binutils install incomplete"
	printf '%s\n' "${args[*]}" > "${stamp}.options"
	touch "$stamp"
}

# --------------------------------------------------------------------- gcc --

build_gcc() {
	local extra=()
	local gmp=${GMP_PREFIX:-} mpfr=${MPFR_PREFIX:-} mpc=${MPC_PREFIX:-} isl=${ISL_PREFIX:-}
	if [ -n "$gmp" ]; then extra+=("--with-gmp=${gmp}"); fi
	if [ -n "$mpfr" ]; then extra+=("--with-mpfr=${mpfr}"); fi
	if [ -n "$mpc" ]; then extra+=("--with-mpc=${mpc}"); fi
	if [ -n "$isl" ]; then extra+=("--with-isl=${isl}"); fi
	if [ -n "$IRIX_SYSROOT" ]; then
		extra+=("--with-sysroot=${IRIX_SYSROOT}")
		# libatomic's configure links a pthread probe against -lpthread.
		# The captured 6.5.7m sysroot has pthread.h (so libgcc selects
		# gthr-posix) but no libpthread.so until issue #18 extends
		# sysroot.files, so libatomic cannot build yet. Require both the
		# default-o32 and n32 captures (ADR-0006 names both); libatomic
		# returns automatically once they are present.
		if [ ! -e "${IRIX_SYSROOT}/usr/lib/libpthread.so" ] ||
			[ ! -e "${IRIX_SYSROOT}/usr/lib32/libpthread.so" ]; then
			extra+=("--disable-libatomic")
		fi
	else
		# No sysroot yet (see issue #3): build libgcc in freestanding
		# single-threaded mode so that it does not need target headers.
		# The pthread.h probe sees the host's header even for a cross
		# build, which would otherwise select the posix thread model.
		# libatomic and libquadmath link against target libc, so they
		# cannot be built until the sysroot exists.
		extra+=("--without-headers" "--disable-threads"
			"--disable-libatomic" "--disable-libquadmath")
	fi

	local args=(
		--prefix="$PREFIX"
		--target="$TARGET"
		--enable-languages="$LANGUAGES"
		--enable-multilib
		--disable-bootstrap
		--disable-nls
		--disable-werror
		--disable-libssp
		--disable-lto
		--disable-pgo-build
		--disable-plugins
		--with-gnu-as
		--with-gnu-ld
		--with-as="${PREFIX}/bin/${TARGET}-as"
		--with-ld="${PREFIX}/bin/${TARGET}-ld"
		"${extra[@]}"
	)

	local stamp=${STAMPS}/gcc-${GCC_VERSION}.installed
	if [ -f "$stamp" ] && [ -x "${PREFIX}/bin/${TARGET}-gcc" ] &&
		[ "$(cat "${stamp}.options" 2>/dev/null || true)" = "${args[*]}" ]; then
		note "GCC ${GCC_VERSION} already installed"
		return 0
	fi

	note "GCC ${GCC_VERSION} (${LANGUAGES}, ${GCC_RECIPE} recipe)"
	fetch "https://ftp.gnu.org/gnu/gcc/gcc-${GCC_VERSION}/${GCC_TARBALL}" \
		"${DOWNLOADS}/${GCC_TARBALL}" "$GCC_SHA256"
	extract "${DOWNLOADS}/${GCC_TARBALL}" "gcc-${GCC_VERSION}"
	local src="${SRC_DIR}/gcc-${GCC_VERSION}"
	if [ "$GCC_RECIPE" = series ]; then
		apply_series "$src" "${GCC_SERIES_DIR}/series"
	else
		fetch_patches "${GCC_15X_PATCHES[@]}"
		apply_patches "$src" "${PATCH_FILES[@]}" "${LOCAL_GCC_PATCHES[@]}"
	fi

	# The just-built binutils must win over any host as/ld.
	export PATH="${PREFIX}/bin:${PATH}"

	configure_and_make "$src" "${BUILD_DIR}/gcc" \
		"${LOGS}/gcc.log" "${args[@]}"

	[ -x "${PREFIX}/bin/${TARGET}-gcc" ] || die "GCC install incomplete"
	printf '%s\n' "${args[*]}" > "${stamp}.options"
	touch "$stamp"
}

# -------------------------------------------------------------------------- --

note "IRIX 6.5 cross toolchain"
echo "    target:   ${TARGET}"
echo "    gcc:      ${GCC_VERSION} (${GCC_RECIPE} recipe)"
echo "    work dir: ${WORK_DIR}"
echo "    prefix:   ${PREFIX}"
echo "    jobs:     ${JOBS}"
echo "    sysroot:  ${IRIX_SYSROOT:-<none>}"

build_binutils
build_gcc

note "Verifying the toolchain"
"${SCRIPT_DIR}/verify-toolchain.sh" --prefix "$PREFIX" \
	--gcc-version "$GCC_VERSION"

note "Done"
echo "    prefix: ${PREFIX}"
echo "    use:    ${PREFIX}/bin/${TARGET}-gcc -mabi=32|n32|64"
