#!/usr/bin/env bash
#
# Build one tree command natively in the guest with MIPSpro + the tree's own
# smake rules, and keep the binary, object, logs and output as the reference a
# later GCC build is diffed against (ADR-0005).
#
# This is the generalisation of hinv-reference.sh (issue #7) to any leaf whose
# Makefile pulls in commondefs/commonrules. It stages the whole source
# directory, runs the smake dry-run and the verbose build, runs the rebuilt and
# the shipped binary, and captures everything under $RIG_ORACLE_DIR/<target>-reference/.
#
# The point is ground truth, not the binary: every flag, include and link step
# is captured verbatim. Nothing is installed into /usr/bin.
#
# Usage:
#   scripts/rig/reference-build.sh --tree DIR --src-dir eoe/cmd/banner --target banner
#   scripts/rig/reference-build.sh --tree DIR --src-dir irix/cmd/hinv --target hinv \
#       --lcincludes "-I$stage/irix/kern -I$stage/irix/usr/include" \
#       --extra-files "irix/kern/sys/EVEREST/diagval_strs.i ..."
#
# Options:
#   --tree DIR        IRIX source checkout (or set IRIX_SRC_TREE)
#   --src-dir DIR     tree-relative source directory (must contain a Makefile)
#   --target NAME     make target / resulting binary name
#   --stock PATH      shipped binary to diff against (default /usr/bin/<target>)
#   --lcincludes STR  LCINCS override, e.g. for staged kernel-only headers
#   --extra-files ..  extra tree-relative files to stage beside the source dir
#   --no-run          build only; do not run the binaries
#
# Every guest transaction takes the rig's shared guest lock.
#
set -euo pipefail

# shellcheck source=scripts/rig/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ORACLE_DRIVER="$RIG_REPO_ROOT/scripts/rig/oracle-driver.py"

TREE=${IRIX_SRC_TREE:-}
SRC_DIR=
TARGET=
STOCK=
LCINCS=
EXTRA_FILES=()
RUN_ARG=
RUN=1

usage() {
	cat <<'EOF'
Usage: scripts/rig/reference-build.sh --tree DIR --src-dir DIR --target NAME [options]

Build one tree command natively in the guest (MIPSpro + smake) and capture the
reference artefacts under $RIG_ORACLE_DIR/<target>-reference/.

  --tree DIR        IRIX source checkout (or set IRIX_SRC_TREE)
  --src-dir DIR     tree-relative source directory with a Makefile
  --target NAME     make target / binary name
  --stock PATH      shipped binary to diff against (default /usr/bin/<target>)
  --lcincludes STR  LCINCS override for staged headers
  --extra-files ..  extra tree-relative files to stage (repeatable)
  --run-arg ARG     argument passed to both binaries when running
  --no-run          build only, do not run the binaries
  -h, --help        this help
EOF
}

while [ $# -gt 0 ]; do
	case "$1" in
		--tree)        TREE=$2; shift 2 ;;
		--tree=*)      TREE=${1#--tree=}; shift ;;
		--src-dir)     SRC_DIR=$2; shift 2 ;;
		--src-dir=*)   SRC_DIR=${1#--src-dir=}; shift ;;
		--target)      TARGET=$2; shift 2 ;;
		--target=*)    TARGET=${1#--target=}; shift ;;
		--stock)       STOCK=$2; shift 2 ;;
		--stock=*)     STOCK=${1#--stock=}; shift ;;
		--lcincludes)  LCINCS=$2; shift 2 ;;
		--lcincludes=*) LCINCS=${1#--lcincludes=}; shift ;;
		--extra-files) EXTRA_FILES+=("$2"); shift 2 ;;
		--run-arg)     RUN_ARG=$2; shift 2 ;;
		--no-run)      RUN=0; shift ;;
		-h|--help)     usage; exit 0 ;;
		*) usage >&2; rig_die "unknown option: $1" ;;
	esac
done

[ -n "$TREE" ] || { usage >&2; rig_die "no tree: pass --tree DIR or set IRIX_SRC_TREE"; }
[ -n "$SRC_DIR" ] || { usage >&2; rig_die "no --src-dir"; }
[ -n "$TARGET" ] || { usage >&2; rig_die "no --target"; }
[ -f "$TREE/$SRC_DIR/Makefile" ] || rig_die "no Makefile at $TREE/$SRC_DIR/Makefile"
STOCK=${STOCK:-/usr/bin/$TARGET}

REF_DIR="$RIG_ORACLE_DIR/${TARGET}-reference"
GUEST_STAGE="/tmp/${TARGET}-reference"
GUEST_BUILD="$GUEST_STAGE/$SRC_DIR"
TAR_NAME="${TARGET}-reference.tar"

# Guest files pulled back, "guest path local name".
PULLS=(
	"$GUEST_STAGE/environment.txt environment.txt"
	"$GUEST_BUILD/$TARGET $TARGET"
	"$GUEST_BUILD/$TARGET.o $TARGET.o"
	"$GUEST_STAGE/$TARGET.output $TARGET.output"
	"$GUEST_STAGE/$TARGET.stock.output $TARGET.stock.output"
	"$GUEST_STAGE/smake.dryrun.log smake.dryrun.log"
	"$GUEST_STAGE/smake.object.log smake.object.log"
	"$GUEST_STAGE/smake.verbose.log smake.verbose.log"
)
# The overrides log only exists when an LCINCS override was requested.
[ -n "$LCINCS" ] && PULLS+=("$GUEST_STAGE/smake.dryrun.overrides.log smake.dryrun.overrides.log")

ic() {
	IRIS_SOCKET="$RIG_SOCKET" rig_with_guest_lock \
		"$RIG_IRIS_CI" --socket "$RIG_SOCKET" "$@"
}

rig_ensure_running() {
	rig_require_iris
	rig_running || "$RIG_REPO_ROOT/scripts/rig/start-rig.sh"
}

rig_ensure_shell() {
	IRIS_SOCKET="$RIG_SOCKET" rig_with_guest_lock \
		python3 "$ORACLE_DRIVER" --socket "$RIG_SOCKET" --log "$RIG_DRIVER_LOG" \
		--ic "$RIG_IRIS_CI" ensure-shell --quiet
}

rig_ensure_running
rig_ensure_shell
mkdir -p "$REF_DIR"

rig_log "staging $SRC_DIR under $GUEST_STAGE"

# Stage the source directory (and any extra files) as one tarball: simpler and
# safer than per-file puts for directories with several files.
stage_files=("$SRC_DIR" "${EXTRA_FILES[@]}")
for f in "${stage_files[@]}"; do
	[ -e "$TREE/$f" ] || rig_die "tree path missing: $TREE/$f"
done
tar -cf "/tmp/$TAR_NAME" -C "$TREE" "${stage_files[@]}"

ic run "cd /tmp; rm -rf $GUEST_STAGE && mkdir -p $GUEST_STAGE" --timeout 120
ic put "/tmp/$TAR_NAME" --to "/tmp/$TAR_NAME" --timeout 300
ic run "cd $GUEST_STAGE; tar -xf /tmp/$TAR_NAME" --timeout 300
rm -f "/tmp/$TAR_NAME"

rig_log "recording smake dry runs"
ic run "cd $GUEST_BUILD; env ROOT=/ smake -n >& $GUEST_STAGE/smake.dryrun.log" --timeout 600
if [ -n "$LCINCS" ]; then
	ic run "cd $GUEST_BUILD; echo '== env ROOT=/ smake -n (LCINCS override) ==' >& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 120
	ic run "cd $GUEST_BUILD; env ROOT=/ smake -n \"LCINCS=$LCINCS\" >>& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 600
	ic run "cd $GUEST_BUILD; echo '== env ROOT=/ smake -n (LCINCS override) $TARGET.o ==' >>& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 120
	ic run "cd $GUEST_BUILD; env ROOT=/ smake -n \"LCINCS=$LCINCS\" $TARGET.o >>& $GUEST_STAGE/smake.dryrun.overrides.log" --timeout 600
fi

SM="\"CC=cc -v\""
[ -n "$LCINCS" ] && SM="\"LCINCS=$LCINCS\" $SM"

rig_log "building $TARGET with cc -v"
ic run "cd $GUEST_BUILD; env ROOT=/ smake $SM >& $GUEST_STAGE/smake.verbose.log" --timeout 1200
ic run "cd $GUEST_BUILD; env ROOT=/ smake $SM $TARGET.o >& $GUEST_STAGE/smake.object.log" --timeout 1200

if [ "$RUN" -eq 1 ]; then
	rig_log "running the rebuilt and stock $TARGET"
	ic run "cd $GUEST_BUILD; ./$TARGET $RUN_ARG > $GUEST_STAGE/$TARGET.output" --timeout 600
	ic run "$STOCK $RUN_ARG > $GUEST_STAGE/$TARGET.stock.output" --timeout 600
fi

rig_log "recording the guest environment"
ic run "cd $GUEST_STAGE; echo '# Reference $TARGET build environment' > environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '# captured:' >>& environment.txt; date >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## uname -a' >>& environment.txt; uname -a >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## cc -version' >>& environment.txt; cc -version >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## versions compiler_dev c_dev c_fe compiler_eoe irix_dev dev | cat' >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; versions compiler_dev c_dev c_fe compiler_eoe irix_dev dev | cat >>& environment.txt" --timeout 600
ic run "cd $GUEST_STAGE; echo '## file $STOCK' >>& environment.txt; file $STOCK >>& environment.txt" --timeout 120
ic run "cd $GUEST_STAGE; echo '## file rebuilt $TARGET' >>& environment.txt; file $SRC_DIR/$TARGET >>& environment.txt" --timeout 120

rig_log "pulling the reference artefacts"
for entry in "${PULLS[@]}"; do
	src=${entry% *}
	dest=${entry##* }
	rm -f "$REF_DIR/$dest"
	ic get "$src" --to "$REF_DIR/$dest" --timeout 600
done

if git -C "$TREE" rev-parse --git-dir >/dev/null 2>&1; then
	rev=$(git -C "$TREE" rev-parse HEAD)
	[ -n "$(git -C "$TREE" status --porcelain --untracked-files=no)" ] && rev="${rev}-dirty"
	printf '%s\n' "$rev" >"$REF_DIR/tree-commit.txt"
else
	echo unknown >"$REF_DIR/tree-commit.txt"
fi

(
	cd "$REF_DIR"
	sha256sum "${PULLS[@]##* }" tree-commit.txt >manifest.sha256
)

rig_log "reference captured under $REF_DIR"
