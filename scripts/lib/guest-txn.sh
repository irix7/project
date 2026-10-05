#!/usr/bin/env bash
#
# Fail-closed guest transaction (issue #5; audit finding C10).
#
# One reusable unit of guest work: create a directory, ship files, run one
# command and retrieve evidence, every stage through iris-ci. smoke.sh, the
# runtime driver and the hinv harnesses call this script instead of
# hand-rolling the sequence, so the transport contract lives in one place:
#
#   * the mkdir, every put, the run and every get call are checked. Any
#     non-zero iris-ci status fails the transaction, even when earlier stages
#     have already produced files, so a failed transport cannot be accepted
#     because matching output exists;
#   * every host evidence path is removed before the first transport call and
#     a failed get removes its own partial target, so an old or truncated
#     result can never satisfy a later failed run;
#   * the transaction's own exit status (below) is distinct from the guest
#     programme's exit status, which is data in the required status file. The
#     status file must have been retrieved and must hold a non-negative
#     integer; callers check its value themselves;
#   * the whole sequence runs under lib.sh's shared guest lock, so callers
#     must not hold it; a nested call is safe through RIG_GUEST_LOCK_HELD.
#
# Usage:
#   scripts/lib/guest-txn.sh --timeout SECONDS \
#       (--streams GUEST_BIN HOST_OUT HOST_ERR HOST_STATUS |
#        --run COMMAND [--get GUEST HOST]... --status GUEST HOST) \
#       --put LOCAL GUEST [--put LOCAL GUEST]... \
#       [--guest-dir DIR] [--label LABEL]
#
# --streams is the smoke and runtime shape: the caller ships GUEST_BIN with
# --put, and this script runs it through sh with stdout, stderr and the exit
# status captured beside it and retrieves all three. The generic form takes
# the exact guest command and evidence list, which is hinv-gcc.sh's shape.
# All guest paths must be absolute and name a unique attempt: callers give
# GUEST_BIN a name with the pid and a random component in a directory this
# script creates.
#
# Transaction exit status:
#   0   every stage succeeded and every evidence file was retrieved
#   64  usage error
#   90  the run failed at the transport (iris-ci non-zero)
#   91  a put or get failed at the transport
#   92  a get reported success but produced no file
#   93  the rig is not answering or the guest directory could not be created
#   94  the guest status file is empty or not a non-negative integer
#
# The guest programme's exit status is the *content* of the status file, not
# this exit status; callers must check both.
#
set -euo pipefail

TXN_REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

TXN_OK=0
TXN_USAGE=64
TXN_RUN_FAIL=90
TXN_TRANSFER_FAIL=91
TXN_EVIDENCE_MISSING=92
TXN_SETUP_FAIL=93
TXN_STATUS_INVALID=94

usage() {
	cat <<'EOF'
Usage: scripts/lib/guest-txn.sh --timeout SECONDS \
	(--streams GUEST_BIN HOST_OUT HOST_ERR HOST_STATUS |
	 --run COMMAND [--get GUEST HOST]... --status GUEST HOST) \
	--put LOCAL GUEST [--put LOCAL GUEST]... \
	[--guest-dir DIR] [--label LABEL]

Fail-closed guest transaction: mkdir, put, run and get through iris-ci, all
under the shared guest lock. Any transport failure fails the transaction even
when evidence files exist; host evidence is cleared before retrieval and the
guest status file must be a non-negative integer.

  --timeout SECONDS        timeout for every iris-ci call (required)
  --streams BIN OUT ERR STATUS
                           run BIN through sh with stdout, stderr and exit
                           status captured beside it, and retrieve all three
                           (ship BIN with --put LOCAL BIN first)
  --run COMMAND            the exact guest command to run (generic form)
  --get GUEST HOST         retrieve GUEST to HOST (generic form, repeatable)
  --status GUEST HOST      the guest programme's exit status file (generic
                           form, required)
  --put LOCAL GUEST        ship LOCAL to GUEST (repeatable, at least one)
  --guest-dir DIR          guest directory to create (default /tmp/smoke)
  --label LABEL            diagnostic label (default guest-txn)
  -h, --help               show this help

Exit status: 0 success; 64 usage; 90 run transport failure; 91 transfer
failure; 92 missing evidence; 93 setup failure; 94 invalid status. The
programme's own status is the status file's content, which callers check.
EOF
}

die() {
	local code=$1
	shift
	echo "guest-txn: $*" >&2
	exit "$code"
}

# Guest paths are interpolated into iris-ci commands, so only accept a plain
# absolute path with no quoting or substitution characters.
require_guest_path() {
	case "$1" in
		/*) ;;
		*) die "$TXN_USAGE" "guest path must be absolute: '$1'" ;;
	esac
	case "$1" in
		*[!A-Za-z0-9_./-]*) die "$TXN_USAGE" "unsafe guest path: '$1'" ;;
	esac
}

timeout=
streams_bin=
streams_out=
streams_err=
streams_status=
run_cmd=
status_guest=
status_host=
guest_dir=/tmp/smoke
label=guest-txn
put_locals=()
put_guests=()
get_guests=()
get_hosts=()

while [ $# -gt 0 ]; do
	case "$1" in
		--timeout)
			[ $# -ge 2 ] || die "$TXN_USAGE" "--timeout needs SECONDS"
			timeout=$2
			shift 2
			;;
		--streams)
			[ $# -ge 5 ] ||
				die "$TXN_USAGE" "--streams needs GUEST_BIN HOST_OUT HOST_ERR HOST_STATUS"
			[ -z "$streams_bin" ] || die "$TXN_USAGE" "--streams given twice"
			streams_bin=$2
			streams_out=$3
			streams_err=$4
			streams_status=$5
			shift 5
			;;
		--run)
			[ $# -ge 2 ] || die "$TXN_USAGE" "--run needs COMMAND"
			run_cmd=$2
			shift 2
			;;
		--get)
			[ $# -ge 3 ] || die "$TXN_USAGE" "--get needs GUEST HOST"
			get_guests+=("$2")
			get_hosts+=("$3")
			shift 3
			;;
		--status)
			[ $# -ge 3 ] || die "$TXN_USAGE" "--status needs GUEST HOST"
			[ -z "$status_guest" ] || die "$TXN_USAGE" "--status given twice"
			status_guest=$2
			status_host=$3
			shift 3
			;;
		--put)
			[ $# -ge 3 ] || die "$TXN_USAGE" "--put needs LOCAL GUEST"
			put_locals+=("$2")
			put_guests+=("$3")
			shift 3
			;;
		--guest-dir)
			[ $# -ge 2 ] || die "$TXN_USAGE" "--guest-dir needs DIR"
			guest_dir=$2
			shift 2
			;;
		--label)
			[ $# -ge 2 ] || die "$TXN_USAGE" "--label needs LABEL"
			label=$2
			shift 2
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			die "$TXN_USAGE" "unknown option: $1 (try --help)"
			;;
	esac
done

[ -n "$timeout" ] || die "$TXN_USAGE" "--timeout is required"
case "$timeout" in
	'' | *[!0-9]*) die "$TXN_USAGE" "bad --timeout: '$timeout'" ;;
esac
[ "$timeout" -gt 0 ] || die "$TXN_USAGE" "--timeout must be positive: $timeout"

require_guest_path "$guest_dir"

if [ -n "$streams_bin" ]; then
	[ -z "$run_cmd" ] || die "$TXN_USAGE" "--streams and --run are mutually exclusive"
	[ -z "$status_guest" ] || die "$TXN_USAGE" "--streams owns the status file; drop --status"
	require_guest_path "$streams_bin"
	run_cmd="sh -c 'chmod +x $streams_bin; $streams_bin > $streams_bin.stdout 2> $streams_bin.stderr; echo \$? > $streams_bin.status'"
	get_guests+=("$streams_bin.stdout")
	get_hosts+=("$streams_out")
	get_guests+=("$streams_bin.stderr")
	get_hosts+=("$streams_err")
	status_guest="$streams_bin.status"
	status_host=$streams_status
else
	[ -n "$run_cmd" ] || die "$TXN_USAGE" "one of --streams or --run is required"
	[ -n "$status_guest" ] || die "$TXN_USAGE" "--status GUEST HOST is required"
fi

[ ${#put_locals[@]} -gt 0 ] || die "$TXN_USAGE" "at least one --put LOCAL GUEST is required"
for ((i = 0; i < ${#put_guests[@]}; i++)); do
	require_guest_path "${put_guests[i]}"
done
for ((i = 0; i < ${#get_guests[@]}; i++)); do
	require_guest_path "${get_guests[i]}"
done
require_guest_path "$status_guest"

# shellcheck source=scripts/rig/lib.sh
source "$TXN_REPO_ROOT/scripts/rig/lib.sh"
rig_require_iris
rig_running ||
	die "$TXN_SETUP_FAIL" \
		"rig is not running (no answer on $RIG_SOCKET); start it with scripts/rig/start-rig.sh"

txn_body() {
	local timeout=$TXN_TIMEOUT
	local guest_dir=$TXN_GUEST_DIR
	local label=$TXN_LABEL
	local i guest host run_rc=0 status

	# Stale evidence is never read: every host target is cleared under the
	# lock before the first transport call.
	for ((i = 0; i < ${#TXN_GET_HOSTS[@]}; i++)); do
		rm -f -- "${TXN_GET_HOSTS[i]}"
	done
	rm -f -- "$TXN_STATUS_HOST"

	ic -q run "mkdir -p $guest_dir" --timeout "$timeout" >/dev/null ||
		die "$TXN_SETUP_FAIL" "$label: cannot create $guest_dir on the guest"

	for ((i = 0; i < ${#TXN_PUT_LOCALS[@]}; i++)); do
		ic -q put "${TXN_PUT_LOCALS[i]}" --to "${TXN_PUT_GUESTS[i]}" \
			--timeout "$timeout" >/dev/null ||
			die "$TXN_TRANSFER_FAIL" \
				"$label: put failed: ${TXN_PUT_LOCALS[i]} -> ${TXN_PUT_GUESTS[i]}"
	done

	# A non-zero iris-ci status here is a transport failure, not the
	# programme's status (that is the status file's content, retrieved
	# below). Fail immediately: no evidence from a broken run is accepted,
	# regardless of what files exist.
	ic -q run "$TXN_RUN" --timeout "$timeout" >/dev/null || run_rc=$?
	[ "$run_rc" -eq 0 ] ||
		die "$TXN_RUN_FAIL" \
			"$label: guest run failed at the transport (iris-ci status $run_rc); evidence not accepted"

	for ((i = 0; i < ${#TXN_GET_GUESTS[@]}; i++)); do
		guest=${TXN_GET_GUESTS[i]}
		host=${TXN_GET_HOSTS[i]}
		ic -q get "$guest" --to "$host" --timeout "$timeout" >/dev/null || {
			rm -f -- "$host"
			die "$TXN_TRANSFER_FAIL" "$label: get failed: $guest -> $host"
		}
		[ -f "$host" ] ||
			die "$TXN_EVIDENCE_MISSING" "$label: get reported success but produced no file: $host"
	done

	ic -q get "$TXN_STATUS_GUEST" --to "$TXN_STATUS_HOST" --timeout "$timeout" >/dev/null || {
		rm -f -- "$TXN_STATUS_HOST"
		die "$TXN_TRANSFER_FAIL" "$label: get failed: $TXN_STATUS_GUEST -> $TXN_STATUS_HOST"
	}
	[ -f "$TXN_STATUS_HOST" ] ||
		die "$TXN_EVIDENCE_MISSING" "$label: get reported success but produced no status file: $TXN_STATUS_HOST"

	status=$(cat -- "$TXN_STATUS_HOST")
	case "$status" in
		'' | *[!0-9]*)
			rm -f -- "$TXN_STATUS_HOST"
			die "$TXN_STATUS_INVALID" \
				"$label: guest status is not a non-negative integer: '$status'"
			;;
	esac
}

TXN_TIMEOUT=$timeout
TXN_GUEST_DIR=$guest_dir
TXN_LABEL=$label
TXN_RUN=$run_cmd
TXN_STATUS_GUEST=$status_guest
TXN_STATUS_HOST=$status_host
TXN_PUT_LOCALS=("${put_locals[@]}")
TXN_PUT_GUESTS=("${put_guests[@]}")
TXN_GET_GUESTS=("${get_guests[@]}")
TXN_GET_HOSTS=("${get_hosts[@]}")

rig_with_guest_lock txn_body
