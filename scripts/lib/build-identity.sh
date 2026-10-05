#!/usr/bin/env bash
#
# Build-identity helpers for resumable toolchain builds (issue #26).
#
# Sourced by scripts/build-toolchain.sh and driven directly by the host-only
# tests in scripts/lib/test-build-identity.py. Every function is pure apart
# from the directories it is explicitly asked to touch.
#
# A resumable cache is only valid when the bytes that produced it are the
# bytes being requested: pinned tarballs, patch bytes in apply order,
# configure arguments, languages, sysroot and the installed tools' own
# --version output. Anything else is a rebuild.

# irix_die MESSAGE...: the single failure path shared by the helpers. The
# caller may override it before use (the tests do, to inspect failures).
irix_die() {
	echo "error: $*" >&2
	exit 1
}

# irix_sha256 FILE: the file's sha256, hex only.
irix_sha256() {
	sha256sum "$1" | awk '{print $1}'
}

# irix_identity_digest: hash the canonical identity lines on stdin.
irix_identity_digest() {
	sha256sum | awk '{print $1}'
}

# irix_default_work_dir REPO_ROOT GCC_VERSION: the version-separated default
# work directory. IRIX_WORK_ROOT (exported by the flake so `nix run` lands in
# the caller's tree) overrides the repo's .scratch; an explicit --work-dir
# always wins over both.
irix_default_work_dir() {
	local repo_root=$1 version=$2
	printf '%s/toolchain-%s\n' "${IRIX_WORK_ROOT:-${repo_root}/.scratch}" "$version"
}

# irix_patch_identity PATCH...: "<sha256>  <basename>" lines in apply order,
# for patches that exist locally (the in-repo series and local deltas).
irix_patch_identity() {
	local p
	for p in "$@"; do
		printf '%s  %s\n' "$(irix_sha256 "$p")" "$(basename "$p")"
	done
}

# irix_pinned_patch_identity NAME:SHA...: the same lines for remote patches
# whose request is pinned by checksum, so identity does not need a download.
irix_pinned_patch_identity() {
	local entry
	for entry in "$@"; do
		printf '%s  %s\n' "${entry##*:}" "${entry%%:*}"
	done
}

# irix_series_patches SERIES: the patch paths named by a series manifest, in
# order, one per line. Fails when a named patch is absent.
irix_series_patches() {
	local series=$1 p
	while IFS= read -r p; do
		case "$p" in
			''|'#'*) continue ;;
		esac
		p="${series%/*}/${p}"
		[ -f "$p" ] || irix_die "series entry not found: ${p}"
		printf '%s\n' "$p"
	done < "$series"
}

# irix_patch_applied SRCDIR PATCH: true when the patch's reverse applies —
# the only reliable already-applied probe. GNU patch's --batch ignores -R
# when it thinks the patch is unreversed, so the probe must not use --batch.
irix_patch_applied() {
	local dir=$1 p=$2
	patch -d "$dir" -p1 --reverse --dry-run --silent < "$p" >/dev/null 2>&1
}

# irix_apply_patches SRCDIR PATCH...
#
# Applies each patch once, recording the applied bytes and their sha256 under
# $SRCDIR/.irix-patched.d/. A patch whose name is already recorded with the
# same hash is skipped; a same-named patch whose bytes changed is reversed
# from the recorded copy and the new bytes applied; when the recorded bytes
# cannot be reversed the caller is asked for --clean rather than keeping an
# unknown mixture. Filename-only markers from older builds are adopted only
# when the requested patch is provably already applied.
irix_apply_patches() {
	local dir=$1
	shift
	local marker_dir="${dir}/.irix-patched.d"
	mkdir -p "$marker_dir"
	local p name sha stored reversed=0
	for p in "$@"; do
		name=$(basename "$p")
		sha=$(irix_sha256 "$p")
		stored="${marker_dir}/${name}.applied"
		reversed=0
		if [ -f "${marker_dir}/${name}.sha256" ] &&
			[ "$(cat "${marker_dir}/${name}.sha256")" = "$sha" ]; then
			echo "    already applied ${name}"
			continue
		fi
		if [ -f "${marker_dir}/${name}.sha256" ]; then
			[ -f "$stored" ] ||
				irix_die "recorded patch bytes for ${name} are missing; re-run with --clean"
			echo "    ${name} changed; reversing the applied copy"
			patch -d "$dir" -p1 --batch --reverse --silent < "$stored" ||
				irix_die "${name} changed and the applied copy does not reverse cleanly; re-run with --clean"
			rm -f "${marker_dir}/${name}.sha256" "$stored"
			reversed=1
		elif [ -f "${marker_dir}/${name}" ]; then
			if irix_patch_applied "$dir" "$p"; then
				echo "    adopting the legacy marker for ${name}"
			else
				irix_die "${name} has a filename-only marker whose bytes are unknown; re-run with --clean"
			fi
		fi
		if irix_patch_applied "$dir" "$p"; then
			echo "    already applied ${name}"
		elif ! patch -d "$dir" -p1 --batch --forward --silent < "$p"; then
			if [ "$reversed" = 1 ]; then
				irix_die "${name} did not apply cleanly after the stored copy was reversed; re-run with --clean"
			fi
			irix_die "${name} did not apply cleanly"
		fi
		cp "$p" "$stored"
		irix_sha256 "$p" > "${marker_dir}/${name}.sha256"
		rm -f "${marker_dir}/${name}"
	done
}

# irix_apply_series SRCDIR SERIES-FILE: apply the manifest's patches in order.
irix_apply_series() {
	local dir=$1 series=$2
	local list=() patches
	patches=$(irix_series_patches "$series")
	[ -n "$patches" ] || irix_die "empty series: ${series}"
	mapfile -t list <<<"$patches"
	irix_apply_patches "$dir" "${list[@]}"
}

# irix_capture_versions FILE TOOL...: record each tool's actual --version
# output, so a later run can prove the installed tools still identify the
# same way. Identity alone does not prove what is installed.
irix_capture_versions() {
	local file=$1
	shift
	local tool
	: > "$file"
	for tool in "$@"; do
		printf '== %s ==\n' "$tool" >> "$file"
		"$tool" --version >> "$file" 2>&1
	done
}

# irix_versions_ok FILE TOOL...: true when a fresh capture equals FILE.
irix_versions_ok() {
	local recorded=$1
	shift
	[ -f "$recorded" ] || return 1
	local tmp rc
	tmp=$(mktemp)
	if ! irix_capture_versions "$tmp" "$@"; then
		rm -f "$tmp"
		return 1
	fi
	cmp -s "$tmp" "$recorded"
	rc=$?
	rm -f "$tmp"
	return "$rc"
}

# irix_stamp_ok STAMP_BASE IDENTITY TOOL...: reuse is allowed only when the
# recorded identity is exactly IDENTITY and the installed tools still report
# the recorded --version output. A partial install (stamp without captured
# output, or a tool that vanished) never matches.
irix_stamp_ok() {
	local base=$1 identity=$2
	shift 2
	[ -f "${base}.identity" ] || return 1
	# ${identity} is normally command-substitution output, but accept a
	# trailing newline either way: both sides are newline-canonicalised.
	[ "$(cat "${base}.identity")" = "$(printf '%s' "$identity")" ] || return 1
	[ -f "${base}.output" ] || return 1
	irix_versions_ok "${base}.output" "$@"
}

# irix_write_stamp STAMP_BASE IDENTITY TOOL...: record the identity and the
# actual installed outputs as the completion record.
irix_write_stamp() {
	local base=$1 identity=$2
	shift 2
	printf '%s\n' "$identity" > "${base}.identity"
	irix_capture_versions "${base}.output" "$@"
}

# irix_gcc_version_conflicts WORK_DIR VERSION: print artefacts under WORK_DIR
# that belong to a different GCC release (stamps, extracted sources, pinned
# tarballs). An explicit work directory must not silently reuse them for a
# different --gcc.
irix_gcc_version_conflicts() {
	local work=$1 version=$2
	local f n v
	for f in "$work"/stamps/gcc-*.installed.identity \
		"$work"/stamps/gcc-*.installed \
		"$work"/src/gcc-* \
		"$work"/downloads/gcc-*.tar.xz; do
		[ -e "$f" ] || continue
		n=${f##*/}
		v=${n#gcc-}
		v=${v%.installed.identity}
		v=${v%.installed}
		v=${v%.tar.xz}
		[ "$v" = "$version" ] || printf '%s\n' "$f"
	done
}

# irix_installed_gcc_version PREFIX TARGET: the first --version line of the
# installed cross compiler, or failure when there is none. Used to refuse a
# prefix that already holds a different release.
irix_installed_gcc_version() {
	local cc="$1/bin/$2-gcc"
	[ -x "$cc" ] || return 1
	"$cc" --version 2>/dev/null | head -n 1
}

# irix_configure_and_make SRCDIR BUILDDIR LOG IDENTITY CONFIGURE-ARGS...
#
# Configures out of tree on first use, and wipes and reconfigures when the
# input identity (patch bytes, options, source) no longer matches the one
# that produced the build tree; then makes and installs. Re-running with the
# same identity completes a partial build and is otherwise a cheap no-op.
irix_configure_and_make() {
	local src=$1 build=$2 log=$3 identity=$4
	shift 4
	local args=("$@")
	local record="${build}/.irix-build-identity"
	mkdir -p "$build"
	if [ -f "${build}/config.status" ]; then
		local current
		current=$(cat "$record" 2>/dev/null || true)
		if [ "$current" != "$(printf '%s' "$identity")" ]; then
			echo "==> build identity changed; reconfiguring ${build##*/}"
			rm -rf "$build"
			mkdir -p "$build"
		fi
	fi
	(
		cd "$build"
		if [ ! -f config.status ]; then
			"${src}/configure" "${args[@]}"
			printf '%s' "$identity" > "$record"
		fi
		make -j"${JOBS:-1}" all
		make install
	) 2>&1 | tee -a "$log"
}
