#!/usr/bin/env bash
#
# Build the IRIX 6.5 cross toolchain.
#
# By default this builds the GCC 16.2 IRIX port: the in-repo binutils 2.47
# IRIX series plus the in-repo GCC 16.2 series (patches/gcc-16.2/), re-derived
# from pdaxrom/irix-gcc tag 1.2's 15.2 IRIX diff:
#
#   * binutils 2.47 with the in-repo IRIX series (patches/binutils-2.47/)
#   * GCC 16.2.0 with the in-repo IRIX series
#   * the local patches/ deltas that a sysroot-less host needs
#
# `--binutils 2.20.1` selects the pdaxrom-patched seed fallback, kept
# unchanged for bisecting and for reproducing pre-2.47 builds;
# `--binutils 2.47` is the default. `--gcc 15.3.0` (or 15.2.0) selects the
# GCC fallback recipe: the pdaxrom 15.2.0 IRIX diffs applied to that
# release plus the local patches. The 15.3.0 path is the heuristic fallback
# documented in docs/toolchain.md; the 15.2.0 path reproduces the original
# baseline tree.
#
# Unlike pdaxrom's own IRIX 6.5 configuration, which builds an n32-default
# compiler for the mips-sgi-irix6n32 triplet, this configures the canonical
# mips-sgi-irix6.5 target with o32 as the default ABI (the Indy's native
# environment, per ADR-0003). The n32-default patch is therefore not
# applied; -mabi=32/-mabi=n32/-mabi=64 still select all three multilibs.
#
# Resumption is bound to identity (issue #26): each component's completion
# stamp records the pinned tarball sha256, the digest of every patch in
# apply order, the recipe, languages, configure arguments, sysroot and the
# actual `--version` output of the installed tools. A stamp is reused only
# while all of that still matches; a changed patch, option or installed
# tool rebuilds. Applied patches keep a copy of their bytes so a same-named
# patch whose bytes changed is reversed before the new bytes are applied.
# A work directory holding another GCC release is refused rather than
# reusing its configured source.
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
#                    (default: <work root>/toolchain-<gcc version>)
#   --prefix DIR     installation prefix (default: <work-dir>/prefix)
#   --jobs N         parallel make jobs (default: number of CPUs)
#   --sysroot DIR    target sysroot; enables libstdc++ if it is set
#   --languages L    GCC languages (default: c, or c,c++ with a sysroot)
#   --gcc VERSION    GCC release: 16.2.0 (default), 15.3.0 or 15.2.0
#   --binutils VER   binutils release: 2.47 (default, IRIX series) or 2.20.1
#                    (pdaxrom patches, seed fallback)
#   --clean          remove the work directory before building
#   -h, --help       show this help
#
# Environment: CC, CXX, GMP_PREFIX, MPFR_PREFIX, MPC_PREFIX, ISL_PREFIX,
# GCC_VERSION (overridden by --gcc), BINUTILS_VERSION (overridden by
# --binutils) and IRIX_WORK_ROOT are honoured. The work root defaults to
# <repo>/.scratch; the flake's `nix run` exports IRIX_WORK_ROOT="$PWD/.scratch"
# so the default follows the caller's tree. The nix devshell sets the first
# five.
#
set -euo pipefail

TARGET=mips-sgi-irix6.5

# GNU binutils releases this script knows how to build. 2.47 uses the in-repo
# IRIX series (recipe `series`); 2.20.1 is the pdaxrom-patched seed fallback
# (recipe `pdaxrom`), kept for bisecting. The
# sha256 values are pinned from the official release tarballs after
# verifying them against the official sha512 list
# (https://sourceware.org/pub/binutils/releases/sha512.sum).
BINUTILS_VERSION=${BINUTILS_VERSION:-2.47}
BINUTILS_SHA256_2_47=154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff
BINUTILS_SHA256_2_20_1=71d37c96451333c5c0b84b170169fdcb138bbb27397dc06281905d9717c8ed64

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

# The 2.20.1 recipe's pdaxrom patches (name:sha256 pairs, downloaded from
# PDAXROM_RAW). Binutils 2.47 uses its in-repo series below.
BINUTILS_2_20_1_PATCHES=(
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

# shellcheck source=scripts/lib/build-identity.sh
source "${SCRIPT_DIR}/lib/build-identity.sh"

# The IRIX OS layer as a git fork of rust-lang/gcc (GCC 17 trunk); see
# ADR-0019. `--gcc fork` (or `--gcc 17`) builds it directly: the IRIX commits
# are already in its history, so no patch series is applied. Override the
# checkout with --gcc-fork or GCC_FORK_DIR.
GCC_FORK_DIR=${GCC_FORK_DIR:-${REPO_ROOT}/.scratch/rust-lang-gcc}
GCC_FORK_VERSION=${GCC_FORK_VERSION:-17.0.0}

# The proven binutils 2.47 IRIX patch series; the manifest defines apply order.
BINUTILS_SERIES_DIR=${REPO_ROOT}/patches/binutils-2.47

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
Build the IRIX 6.5 cross toolchain (patched binutils 2.47 + GCC 16.2.0 by
default) targeting mips-sgi-irix6.5 with big-endian o32, n32 and n64
multilibs. Binutils 2.47 uses the in-repo IRIX series; --binutils 2.20.1
selects the pdaxrom-patched seed fallback. The gcc-16.2 IRIX series is
in-repo; --gcc selects the 15.3.0 or 15.2.0 pdaxrom fallback recipe, or
`fork` to build the irix7/gcc 17-trunk fork directly (IRIX commits already
in its history), adding `jit` for the libgccjit the Rust backend needs.

Usage: scripts/build-toolchain.sh [options]

  --work-dir DIR   scratch space for sources, builds and logs
                   (default: <work root>/toolchain-<gcc version>)
  --prefix DIR     installation prefix (default: <work-dir>/prefix)
  --jobs N         parallel make jobs (default: number of CPUs)
  --sysroot DIR    target sysroot; enables libstdc++ if it is set
  --languages L    GCC languages (default: c, or c,c++ with a sysroot)
  --gcc VERSION    GCC release: 16.2.0 (default), 15.3.0, 15.2.0, or 17/fork
                   (the irix7/gcc fork: rust-lang/gcc 17 trunk + the IRIX
                   commits + the patched libgccjit the Rust backend needs)
  --gcc-fork DIR   build from this GCC fork checkout (implies --gcc fork;
                   default .scratch/rust-lang-gcc)
  --binutils VER   binutils release: 2.47 (default, IRIX series) or 2.20.1
                   (pdaxrom patches, seed fallback)
  --clean          remove the work directory before building
  -h, --help       show this help

Environment: CC, CXX, GMP_PREFIX, MPFR_PREFIX, MPC_PREFIX, ISL_PREFIX,
GCC_VERSION (overridden by --gcc), BINUTILS_VERSION (overridden by
--binutils) and IRIX_WORK_ROOT are honoured. The work root defaults to
<repo>/.scratch; the flake exports IRIX_WORK_ROOT="$PWD/.scratch" so nix
run follows the caller's tree.
EOF
}

die() {
	irix_die "$@"
}

note() {
	printf '\n==> %s\n' "$*"
}

parse_args() {
	while [ $# -gt 0 ]; do
		case "$1" in
			--work-dir) WORK_DIR=$2; shift 2 ;;
			--prefix) PREFIX=$2; shift 2 ;;
			--jobs) JOBS=$2; shift 2 ;;
			--sysroot) IRIX_SYSROOT=$2; shift 2 ;;
			--languages) LANGUAGES=$2; shift 2 ;;
			--gcc) GCC_VERSION=$2; shift 2 ;;
			--gcc-fork) GCC_VERSION=17.0.0; GCC_FORK_DIR=$2; shift 2 ;;
			--binutils) BINUTILS_VERSION=$2; shift 2 ;;
			--clean) CLEAN=1; shift ;;
			-h|--help) usage; exit 0 ;;
			*) die "unknown option: $1 (try --help)" ;;
		esac
	done
}

# resolve_binutils_recipe: 2.47 uses the in-repo IRIX series; 2.20.1 is the
# pdaxrom-patched seed fallback. The tarball name, pinned sha256, recipe name
# and patch list are per release, so identity and reuse distinguish them.
resolve_binutils_recipe() {
	local patches
	BINUTILS_SERIES_PATCHES=()
	case "$BINUTILS_VERSION" in
		2.47)
			BINUTILS_SHA256=$BINUTILS_SHA256_2_47
			BINUTILS_RECIPE=series
			BINUTILS_TARBALL=binutils-2.47.tar.xz
			BINUTILS_SERIES_FILE=${BINUTILS_SERIES_DIR}/series
			BINUTILS_PATCHES=()
			patches=$(irix_series_patches "$BINUTILS_SERIES_FILE")
			[ -n "$patches" ] || die "empty binutils series: ${BINUTILS_SERIES_FILE}"
			mapfile -t BINUTILS_SERIES_PATCHES <<<"$patches"
			;;
		2.20.1)
			BINUTILS_SHA256=$BINUTILS_SHA256_2_20_1
			BINUTILS_RECIPE=pdaxrom
			BINUTILS_TARBALL=binutils-2.20.1.tar.bz2
			BINUTILS_SERIES_FILE=
			BINUTILS_PATCHES=("${BINUTILS_2_20_1_PATCHES[@]}")
			;;
		*)
			die "unsupported binutils version: ${BINUTILS_VERSION} (known: 2.47, 2.20.1)"
			;;
	esac
}

# resolve_recipe: 16.2.0 is the in-repo series; 15.3.0 and 15.2.0 use the
# pdaxrom 15.2.0 diffs.
resolve_recipe() {
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
		17|17.0.0|fork)
			GCC_VERSION=$GCC_FORK_VERSION
			GCC_RECIPE=fork
			[ -d "$GCC_FORK_DIR" ] ||
				die "GCC fork checkout not found: $GCC_FORK_DIR (clone irix7/gcc, or pass --gcc-fork)"
			;;
		*)
			die "unsupported GCC version: ${GCC_VERSION} (known: 17.0.0/fork, 16.2.0, 15.3.0, 15.2.0)"
			;;
	esac
	if [ "$GCC_RECIPE" = fork ]; then
		GCC_TARBALL=
	else
		GCC_TARBALL="gcc-${GCC_VERSION}.tar.xz"
	fi
}

prepare_work_dir() {
	WORK_DIR=${WORK_DIR:-$(irix_default_work_dir "$REPO_ROOT" "$GCC_VERSION")}

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
}

# check_version_conflicts: an explicit work directory shared across releases
# is refused by name, never silently reused, and a prefix holding another
# release's compiler can never be reported as the requested one.
check_version_conflicts() {
	local conflicts default
	conflicts=$(irix_gcc_version_conflicts "$WORK_DIR" "$GCC_VERSION")
	if [ -n "$conflicts" ]; then
		default=$(irix_default_work_dir "$REPO_ROOT" "$GCC_VERSION")
		die "work dir ${WORK_DIR} holds artefacts from another GCC release:
${conflicts}
choose the version-separated default ${default} or pass --clean"
	fi
	local installed
	if installed=$(irix_installed_gcc_version "$PREFIX" "$TARGET"); then
		case "$installed" in
			*"$GCC_VERSION"*) ;;
			*)
				die "installed compiler in ${PREFIX} reports '${installed}', not GCC ${GCC_VERSION}; remove it, choose a fresh --prefix or pass --clean"
				;;
		esac
	fi
}

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

# binutils_identity CONFIGURE-ARGS...: the full requested identity, including
# the pinned patch checksums so it is knowable without a download. Version,
# tarball sha256, recipe and patch digests all participate, so a changed
# --binutils or a changed patch never reuses another release's stamp.
binutils_identity() {
	printf 'component=binutils\n'
	printf 'version=%s\n' "$BINUTILS_VERSION"
	printf 'tarball_sha256=%s\n' "$BINUTILS_SHA256"
	printf 'recipe=%s\n' "$BINUTILS_RECIPE"
	printf 'patches:\n'
	if [ "$BINUTILS_RECIPE" = series ]; then
		irix_patch_identity "${BINUTILS_SERIES_PATCHES[@]}"
	else
		irix_pinned_patch_identity "${BINUTILS_PATCHES[@]}"
	fi
	printf 'configure:\n'
	printf '%s\n' "$@"
}

# gcc_identity CONFIGURE-ARGS...: the full requested identity, including the
# digest of every patch in apply order.
gcc_identity() {
	local patches list=()
	printf 'component=gcc\n'
	printf 'version=%s\n' "$GCC_VERSION"
	printf 'recipe=%s\n' "$GCC_RECIPE"
	printf 'languages=%s\n' "$LANGUAGES"
	printf 'sysroot=%s\n' "${IRIX_SYSROOT:-<none>}"
	if [ "$GCC_RECIPE" = fork ]; then
		printf 'fork_commit=%s\n' "$(git -C "$GCC_FORK_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
	else
		printf 'tarball_sha256=%s\n' "$GCC_SHA256"
		printf 'patches:\n'
		if [ "$GCC_RECIPE" = series ]; then
			patches=$(irix_series_patches "${GCC_SERIES_DIR}/series")
			[ -n "$patches" ] || irix_die "empty series: ${GCC_SERIES_DIR}/series"
			mapfile -t list <<<"$patches"
			irix_patch_identity "${list[@]}"
		else
			irix_pinned_patch_identity "${GCC_15X_PATCHES[@]}"
			irix_patch_identity "${LOCAL_GCC_PATCHES[@]}"
		fi
	fi
	printf 'configure:\n'
	printf '%s\n' "$@"
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

	local identity stamp
	identity=$(binutils_identity "${args[@]}")
	stamp=${STAMPS}/binutils.installed
	if irix_stamp_ok "$stamp" "$identity" \
		"${PREFIX}/bin/${TARGET}-as" "${PREFIX}/bin/${TARGET}-ld"; then
		note "binutils ${BINUTILS_VERSION} already installed"
		return 0
	fi

	note "binutils ${BINUTILS_VERSION} (${BINUTILS_RECIPE} recipe)"
	fetch "https://ftp.gnu.org/gnu/binutils/${BINUTILS_TARBALL}" \
		"${DOWNLOADS}/${BINUTILS_TARBALL}" "$BINUTILS_SHA256"
	extract "${DOWNLOADS}/${BINUTILS_TARBALL}" "binutils-${BINUTILS_VERSION}"
	local src="${SRC_DIR}/binutils-${BINUTILS_VERSION}"
	if [ "$BINUTILS_RECIPE" = series ]; then
		irix_apply_series "$src" "$BINUTILS_SERIES_FILE"
	else
		fetch_patches "${BINUTILS_PATCHES[@]}"
		irix_apply_patches "$src" "${PATCH_FILES[@]}"
	fi

	irix_configure_and_make "$src" "${BUILD_DIR}/binutils" \
		"${LOGS}/binutils.log" "$identity" "${args[@]}"

	[ -x "${PREFIX}/bin/${TARGET}-as" ] || die "binutils install incomplete"
	irix_write_stamp "$stamp" "$identity" \
		"${PREFIX}/bin/${TARGET}-as" "${PREFIX}/bin/${TARGET}-ld"
}

# --------------------------------------------------------------------- gcc --

# gcc_extra_configure_args: the target configure arguments that depend on
# whether a captured sysroot is present, one per line for mapfile.
#
# With a sysroot, libatomic is built. Its configure links a target executable
# (AC_LINK_IFELSE), so it needs the same working non-shared target link as any
# other program. The GCC 17 fork's IRIX specs select the SGI-ld branch unless
# IRIX_USING_GNU_LD is defined, which made every such link pass -no_unresolved
# to binutils 2.47's GNU ld (issue #150). The fork now defines
# IRIX_USING_GNU_LD unconditionally for IRIX (commit dba1077c5), so the probe
# passes and libgo's 32-bit atomics have their runtime; the old
# libpthread-proxy heuristic is gone.
gcc_extra_configure_args() {
	if [ -n "$IRIX_SYSROOT" ]; then
		printf '%s\n' "--with-sysroot=${IRIX_SYSROOT}"
	else
		# No sysroot yet (see issue #3): build libgcc in freestanding
		# single-threaded mode so that it does not need target headers.
		# The pthread.h probe sees the host's header even for a cross
		# build, which would otherwise select the posix thread model.
		# libatomic and libquadmath link against target libc, so they
		# cannot be built until the sysroot exists.
		printf '%s\n' "--without-headers" "--disable-threads" \
			"--disable-libatomic" "--disable-libquadmath"
	fi
}

build_gcc() {
	local extra=()
	local gmp=${GMP_PREFIX:-} mpfr=${MPFR_PREFIX:-} mpc=${MPC_PREFIX:-} isl=${ISL_PREFIX:-}
	if [ -n "$gmp" ]; then extra+=("--with-gmp=${gmp}"); fi
	if [ -n "$mpfr" ]; then extra+=("--with-mpfr=${mpfr}"); fi
	if [ -n "$mpc" ]; then extra+=("--with-mpc=${mpc}"); fi
	if [ -n "$isl" ]; then extra+=("--with-isl=${isl}"); fi
	local sysargs=()
	mapfile -t sysargs < <(gcc_extra_configure_args)
	extra+=("${sysargs[@]}")

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
	# libgccjit (the Rust codegen backend, ADR-0018) must be a PIC shared
	# library, and release checking keeps the build bearable. See the GCC
	# libgccjit docs, "Working on the JIT library".
	if [[ ",${LANGUAGES}," == *",jit,"* ]]; then
		args+=("--enable-host-shared" "--enable-checking=release")
	fi

	local identity stamp
	identity=$(gcc_identity "${args[@]}")
	stamp=${STAMPS}/gcc-${GCC_VERSION}.installed
	if irix_stamp_ok "$stamp" "$identity" "${PREFIX}/bin/${TARGET}-gcc"; then
		note "GCC ${GCC_VERSION} already installed"
		return 0
	fi

	note "GCC ${GCC_VERSION} (${LANGUAGES}, ${GCC_RECIPE} recipe)"
	local src
	if [ "$GCC_RECIPE" = fork ]; then
		src=$(cd "$GCC_FORK_DIR" && pwd)
		echo "    using fork checkout ${src} at $(git -C "$src" rev-parse --short HEAD 2>/dev/null || echo '?')"
		[ -x "${src}/configure" ] ||
			die "fork checkout has no generated configure: run 'autoconf' in ${src} (or contrib/gcc_update) first"
	else
		fetch "https://ftp.gnu.org/gnu/gcc/gcc-${GCC_VERSION}/${GCC_TARBALL}" \
			"${DOWNLOADS}/${GCC_TARBALL}" "$GCC_SHA256"
		extract "${DOWNLOADS}/${GCC_TARBALL}" "gcc-${GCC_VERSION}"
		src="${SRC_DIR}/gcc-${GCC_VERSION}"
		if [ "$GCC_RECIPE" = series ]; then
			irix_apply_series "$src" "${GCC_SERIES_DIR}/series"
		else
			fetch_patches "${GCC_15X_PATCHES[@]}"
			irix_apply_patches "$src" "${PATCH_FILES[@]}" "${LOCAL_GCC_PATCHES[@]}"
		fi
	fi

	# The just-built binutils must win over any host as/ld.
	export PATH="${PREFIX}/bin:${PATH}"

	irix_configure_and_make "$src" "${BUILD_DIR}/gcc" \
		"${LOGS}/gcc.log" "$identity" "${args[@]}"

	[ -x "${PREFIX}/bin/${TARGET}-gcc" ] || die "GCC install incomplete"
	irix_write_stamp "$stamp" "$identity" "${PREFIX}/bin/${TARGET}-gcc"
}

# -------------------------------------------------------------------------- --

main() {
	parse_args "$@"
	resolve_recipe
	resolve_binutils_recipe
	prepare_work_dir
	check_version_conflicts

	note "IRIX 6.5 cross toolchain"
	echo "    target:   ${TARGET}"
	echo "    binutils: ${BINUTILS_VERSION} (${BINUTILS_RECIPE} recipe)"
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
}

# Sourced by the identity tests; executed directly otherwise.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	main "$@"
fi
