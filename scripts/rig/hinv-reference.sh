#!/usr/bin/env bash
#
# Build the tree's hinv natively in the guest with MIPSpro and the tree's own
# smake rules, and keep the binary, object, logs and output as the project's
# reference for the toolchain rebuild (issue #7, ADR-0005).
#
# The point is ground truth, not the binary: every flag, include and link step
# that produced a real IRIX /sbin/hinv is captured verbatim, so a later GCC
# build can be diffed against it instead of against a guess.
#
# The 6.5 Development Libraries install ships most of the headers hinv needs
# under /usr/include/sys/EVEREST, but not the kernel-only diagval_strs.i, and
# it ships invent.h without diskinvent.h. Both live in the source tree, so the
# script stages just those files under /tmp/hinv-reference/<tree path> and
# prepends the two directories with an LCINCS override. Everything is built in
# /tmp only; nothing is installed.
#
# The ABI is the tree's own default for this release: n32. releasedefs sets
# DEF_OBJECT_STYLE=N32_M3 and the guest's shipped /sbin/hinv is ELF N32 MSB
# mips-3, so n32 is ground truth here even though the IP22 userland also runs
# o32. The verbose build log carries the phase-level flags (-TARG:abi=n32).
#
# Artefacts under $RIG_ORACLE_DIR/hinv-reference/ are SGI material and stay
# outside the repo (ADR-0001); only this script and docs/reference-hinv.md are
# committed.
#
# Usage: scripts/rig/hinv-reference.sh --tree /path/to/irix-6.5.7m-src
#        IRIX_SRC_TREE=/path/to/tree scripts/rig/hinv-reference.sh
#
# Every guest transaction takes the rig's shared guest lock, because a parallel
# agent may be driving the same emulator; do not wrap the whole script in that
# same lock.
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ORACLE_DRIVER="$RIG_REPO_ROOT/scripts/rig/oracle-driver.py"
REF_DIR="$RIG_ORACLE_DIR/hinv-reference"

# Guest scratch: the source tree is staged here with tree-relative paths so the
# include override can point at the tree's irix/kern and irix/usr/include.
GUEST_STAGE=/tmp/hinv-reference
GUEST_BUILD="$GUEST_STAGE/irix/cmd/hinv"
GUEST_LCINCS="-I$GUEST_STAGE/irix/kern -I$GUEST_STAGE/irix/usr/include"

# The tree files this build actually needs, tree-relative. diagval_strs.i is a
# kernel-only include the dev install omits; evdiag.h is its dependency (the
# guest has an identical copy, but staging it keeps the pair together);
# diskinvent.h is a userland header the install does not ship.
TREE_FILES=(
	irix/cmd/hinv/hinv.c
	irix/cmd/hinv/Makefile
	irix/kern/sys/EVEREST/diagval_strs.i
	irix/kern/sys/EVEREST/evdiag.h
	irix/usr/include/diskinvent.h
)

# Guest files pulled back, each as "guest path local name" (one space). The
# binary and object live in the build directory; the logs and outputs in the
# staging root.
PULLS=(
	"$GUEST_STAGE/environment.txt environment.txt"
	"$GUEST_BUILD/hinv hinv"
	"$GUEST_BUILD/hinv.o hinv.o"
	"$GUEST_STAGE/hinv.output hinv.output"
	"$GUEST_STAGE/hinv.stock.output hinv.stock.output"
	"$GUEST_STAGE/smake.dryrun.log smake.dryrun.log"
	"$GUEST_STAGE/smake.dryrun.overrides.log smake.dryrun.overrides.log"
	"$GUEST_STAGE/smake.object.log smake.object.log"
	"$GUEST_STAGE/smake.verbose.log smake.verbose.log"
)

TREE=${IRIX_SRC_TREE:-}

usage() {
	cat <<'EOF'
Usage: scripts/rig/hinv-reference.sh --tree DIR

Build hinv from the IRIX source tree in the guest with MIPSpro + smake and
capture the reference artefacts under $RIG_ORACLE_DIR/hinv-reference/.

Options:
  --tree DIR   IRIX source checkout (or set IRIX_SRC_TREE); must contain
               irix/cmd/hinv/hinv.c
  -h, --help   show this help

The rig must be running; the script starts it if the socket does not answer.
EOF
}

# Take lib.sh's shared guest lock around every emulator transaction. This
# shadows lib.sh's ic so no call in this script can bypass the lock.
ic() {
	IRIS_SOCKET="$RIG_SOCKET" rig_with_guest_lock "$RIG_IRIS_CI" "$@"
}

rig_ensure_running() {
	rig_require_iris
	rig_running || "$RIG_REPO_ROOT/scripts/rig/start-rig.sh"
}

rig_ensure_shell() {
	# IRIS_SOCKET is exported so the driver's own iris-ci boot/login calls hit
	# this rig's socket rather than iris-ci's default.
	IRIS_SOCKET="$RIG_SOCKET" rig_with_guest_lock \
		python3 "$ORACLE_DRIVER" --socket "$RIG_SOCKET" --log "$RIG_DRIVER_LOG" \
		--ic "$RIG_IRIS_CI" ensure-shell --quiet
}

while [ $# -gt 0 ]; do
	case "$1" in
		--tree)
			[ $# -ge 2 ] || rig_die "--tree needs a directory"
			TREE=$2
			shift 2
			;;
		--tree=*)
			TREE=${1#--tree=}
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*) usage >&2; rig_die "unknown option: $1" ;;
	esac
done

[ -n "$TREE" ] || { usage >&2; rig_die "no tree: pass --tree DIR or set IRIX_SRC_TREE"; }
[ -f "$TREE/irix/cmd/hinv/hinv.c" ] || \
	rig_die "not an IRIX tree (no irix/cmd/hinv/hinv.c): $TREE"

rig_ensure_running
rig_ensure_shell
mkdir -p "$REF_DIR"

rig_log "staging tree files under $GUEST_STAGE"
# cd out first: the guest shell persists its cwd across iris-ci runs, and a
# previous run may have left it inside the staging tree being removed.
ic run "cd /tmp; rm -rf $GUEST_STAGE && mkdir -p $GUEST_BUILD $GUEST_STAGE/irix/kern/sys/EVEREST $GUEST_STAGE/irix/usr/include" --timeout 120
for f in "${TREE_FILES[@]}"; do
	[ -f "$TREE/$f" ] || rig_die "tree file missing: $TREE/$f"
	ic put "$TREE/$f" --to "$GUEST_STAGE/$f" --timeout 300
done

# The bare dry run shows what smake resolves with no help at all; the
# overridden one shows the flags the real build uses, including the resolved
# compile (null-suffix) and object rules. Each iris-ci run is kept short on
# purpose: a long command line is silently truncated or wedged by the guest's
# serial input, so every step below is its own run.
rig_log "recording smake dry runs"
ic run "cd $GUEST_BUILD; env ROOT=/ smake -n >& $GUEST_STAGE/smake.dryrun.log" --timeout 600
ic run "cd $GUEST_BUILD; echo '== env ROOT=/ smake -n (LCINCS override) ==' >& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 120
ic run "cd $GUEST_BUILD; env ROOT=/ smake -n \"LCINCS=$GUEST_LCINCS\" >>& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 600
ic run "cd $GUEST_BUILD; echo '== env ROOT=/ smake -n (LCINCS override) hinv.o ==' >>& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 120
ic run "cd $GUEST_BUILD; env ROOT=/ smake -n \"LCINCS=$GUEST_LCINCS\" hinv.o >>& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 600

rig_log "building hinv with cc -v (the tree's n32 default)"
ic run "cd $GUEST_BUILD; env ROOT=/ smake \"LCINCS=$GUEST_LCINCS\" \"CC=cc -v\" >& $GUEST_STAGE/smake.verbose.log" --timeout 1200

# The default rule compiles and links in one driver call and removes the
# temporary hinv.o, so ask for the object explicitly to keep it as evidence.
rig_log "building hinv.o with cc -v"
ic run "cd $GUEST_BUILD; env ROOT=/ smake \"LCINCS=$GUEST_LCINCS\" \"CC=cc -v\" hinv.o >& $GUEST_STAGE/smake.object.log" --timeout 1200

rig_log "running the rebuilt and stock hinv"
ic run "cd $GUEST_BUILD; ./hinv > $GUEST_STAGE/hinv.output" --timeout 600
ic run "/sbin/hinv > $GUEST_STAGE/hinv.stock.output" --timeout 600

rig_log "recording the guest environment"
ic run "cd $GUEST_STAGE; echo '# Reference hinv build environment (issue #7)' > environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '# captured:' >>& environment.txt; date >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## uname -a' >>& environment.txt; uname -a >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## cc -version' >>& environment.txt; cc -version >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## versions compiler_dev c_dev c_fe compiler_eoe irix_dev dev | cat' >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; versions compiler_dev c_dev c_fe compiler_eoe irix_dev dev | cat >>& environment.txt" --timeout 600
ic run "cd $GUEST_STAGE; echo '## versions dev.sw.make | cat' >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; versions dev.sw.make | cat >>& environment.txt" --timeout 300
ic run "cd $GUEST_STAGE; echo '## ls -la /usr/sbin/smake /usr/sbin/pmake' >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; ls -la /usr/sbin/smake /usr/sbin/pmake >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## file /sbin/hinv' >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; file /sbin/hinv >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## file rebuilt hinv' >>& environment.txt; file irix/cmd/hinv/hinv >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## file rebuilt hinv.o' >>& environment.txt; file irix/cmd/hinv/hinv.o >>& environment.txt" --timeout 120

rig_log "pulling the reference artefacts"
for entry in "${PULLS[@]}"; do
	src=${entry% *}
	dest=${entry##* }
	rm -f "$REF_DIR/$dest"
	ic get "$src" --to "$REF_DIR/$dest" --timeout 600
done

# Record what the build actually came from. The commit alone is not the whole
# provenance: a checkout can carry tracked edits or, as here, local additions
# recovered outside the commit (the irix/usr/include supplement), and one of
# those files is compiled into the reference. tree-commit.txt is suffixed
# -dirty when tracked files differ; tree-files.txt records each staged
# source's git status, so a later comparison knows exactly what was built.
if git -C "$TREE" rev-parse --git-dir >/dev/null 2>&1; then
	rev=$(git -C "$TREE" rev-parse HEAD)
	if [ -n "$(git -C "$TREE" status --porcelain --untracked-files=no)" ]; then
		rev="${rev}-dirty"
	fi
	printf '%s\n' "$rev" >"$REF_DIR/tree-commit.txt"

	: >"$REF_DIR/tree-files.txt"
	for f in "${TREE_FILES[@]}"; do
		if ! git -C "$TREE" ls-files --error-unmatch "$f" >/dev/null 2>&1; then
			status=untracked
		elif ! git -C "$TREE" diff --quiet -- "$f"; then
			status=modified
		else
			status=clean
		fi
		printf '%s\t%s\n' "$status" "$f" >>"$REF_DIR/tree-files.txt"
	done
else
	echo unknown >"$REF_DIR/tree-commit.txt"
	echo unknown >"$REF_DIR/tree-files.txt"
fi

(
	cd "$REF_DIR"
	sha256sum "${PULLS[@]##* }" tree-commit.txt tree-files.txt >manifest.sha256
)

rig_log "reference captured under $REF_DIR"
