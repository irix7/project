#!/usr/bin/env bash
#
# Test double for iris-ci (issue #5 fault-injection tests only).
#
# scripts/rig/fake_rig.py copies this into a temporary IRIX_RIG_DIR at
# iris/target/release/iris-ci, which is where lib.sh derives RIG_IRIS_CI from.
# It never touches the live rig, the guest, nix or the network.
#
# The fake keeps a "guest" filesystem under $FAKE_IRIS_STATE, mapping an
# absolute guest path /x/y to $FAKE_IRIS_STATE/guest/x/y, and answers the four
# commands the harnesses use: ping, put, run and get. Behaviour and failures
# are selected per test through the FAKE_* environment; every argv line is
# appended to $FAKE_CALL_LOG when set.
#
#   FAKE_PING_RC                   ping exit status (default 0)
#   FAKE_PUT_FAIL=1                put exits 1 before copying
#   FAKE_RUN_RC=N                  run exits N (transport failure)
#   FAKE_RUN_TIMEOUT=1             run exits 124
#   FAKE_RUN_WRITES_BEFORE_FAIL=1  with FAKE_RUN_RC, write evidence first
#   FAKE_RUN_GUEST_BIN=PATH        guest binary for a non-streams run command
#   FAKE_STDOUT_TEXT / FAKE_STDERR_TEXT
#                                  run writes this literal output
#   FAKE_STDOUT_FILE / FAKE_STDERR_FILE
#                                  run copies this host file as output
#   FAKE_GUEST_STATUS=N            run writes N as the exit status (default 0)
#   FAKE_GUEST_STATUS_TEXT=TEXT    run writes TEXT instead (non-numeric tests)
#   FAKE_STATUS_EMPTY=1            run writes an empty status file
#   FAKE_STATUS_OMIT=1             run writes no status file
#   FAKE_EXTRA_PATH=PATH           run writes an extra guest file at PATH
#   FAKE_EXTRA_TEXT=TEXT           its content (default empty)
#   FAKE_GET_FAIL=stdout|stderr|status|any
#                                  get exits 1 for that evidence kind
#   FAKE_GET_SILENT_SUCCESS=stdout|stderr|status|any
#                                  get exits 0 but writes no target file
#   FAKE_GET_PARTIAL=1             a failing get writes a truncated copy first
#   FAKE_GET_PARTIAL_BYTES=N       truncated copy size (default 1)
#   FAKE_GET_WRITE_THEN_FAIL=1     a failing get writes a complete copy first
#
set -u

state=${FAKE_IRIS_STATE:?FAKE_IRIS_STATE is required}
if [ -n "${FAKE_CALL_LOG:-}" ]; then
	printf '%s\n' "$*" >>"$FAKE_CALL_LOG"
fi

while [ $# -gt 0 ]; do
	case "$1" in
		--socket)
			shift 2
			;;
		-q | --quiet)
			shift
			;;
		*)
			break
			;;
	esac
done

cmd=${1:-}
shift || true

gpath() {
	printf '%s/guest%s' "$state" "$1"
}

# kind_of PATH: stdout, stderr, status, or empty.
kind_of() {
	case "$1" in
		*.stdout) printf 'stdout' ;;
		*.stderr) printf 'stderr' ;;
		*.status) printf 'status' ;;
		*) printf '' ;;
	esac
}

write_streams() {
	local guest_bin=$1
	[ -n "$guest_bin" ] || return 0
	mkdir -p "$(dirname "$(gpath "$guest_bin")")"
	if [ -n "${FAKE_STDOUT_FILE:-}" ]; then
		cp "$FAKE_STDOUT_FILE" "$(gpath "$guest_bin.stdout")"
	else
		printf '%s' "${FAKE_STDOUT_TEXT:-}" >"$(gpath "$guest_bin.stdout")"
	fi
	if [ -n "${FAKE_STDERR_FILE:-}" ]; then
		cp "$FAKE_STDERR_FILE" "$(gpath "$guest_bin.stderr")"
	else
		printf '%s' "${FAKE_STDERR_TEXT:-}" >"$(gpath "$guest_bin.stderr")"
	fi
	if [ "${FAKE_STATUS_OMIT:-0}" != 1 ]; then
		if [ "${FAKE_STATUS_EMPTY:-0}" = 1 ]; then
			: >"$(gpath "$guest_bin.status")"
		elif [ -n "${FAKE_GUEST_STATUS_TEXT:-}" ]; then
			printf '%s' "$FAKE_GUEST_STATUS_TEXT" >"$(gpath "$guest_bin.status")"
		else
			printf '%s' "${FAKE_GUEST_STATUS:-0}" >"$(gpath "$guest_bin.status")"
		fi
	fi
	if [ -n "${FAKE_EXTRA_PATH:-}" ]; then
		mkdir -p "$(dirname "$(gpath "$FAKE_EXTRA_PATH")")"
		printf '%s' "${FAKE_EXTRA_TEXT:-}" >"$(gpath "$FAKE_EXTRA_PATH")"
	fi
}

case "$cmd" in
	ping)
		exit "${FAKE_PING_RC:-0}"
		;;
	put)
		local_file=${1:-}
		shift || true
		guest=
		while [ $# -gt 0 ]; do
			case "$1" in
				--to)
					guest=$2
					shift 2
					;;
				--timeout)
					shift 2
					;;
				*)
					shift
					;;
			esac
		done
		[ "${FAKE_PUT_FAIL:-0}" != 1 ] || exit 1
		[ -f "$local_file" ] || exit 1
		mkdir -p "$(dirname "$(gpath "$guest")")"
		cp "$local_file" "$(gpath "$guest")"
		;;
	run)
		run_cmd=
		while [ $# -gt 0 ]; do
			case "$1" in
				--timeout)
					shift 2
					;;
				*)
					run_cmd=$1
					shift || true
					break
					;;
			esac
		done
		# The transaction's setup call is also a run: it must succeed for
		# the later failure knobs to see the programme run at all.
		case "$run_cmd" in
			"mkdir -p "*)
				exit 0
				;;
		esac
		guest_bin=${FAKE_RUN_GUEST_BIN:-}
		if [ -z "$guest_bin" ]; then
			guest_bin=$(printf '%s\n' "$run_cmd" |
				sed -n 's/.*chmod +x \([^;]*\);.*/\1/p' | head -n 1)
		fi
		[ "${FAKE_RUN_TIMEOUT:-0}" != 1 ] || exit 124
		run_rc=${FAKE_RUN_RC:-0}
		if [ "$run_rc" != 0 ]; then
			[ "${FAKE_RUN_WRITES_BEFORE_FAIL:-0}" != 1 ] ||
				write_streams "$guest_bin"
			exit "$run_rc"
		fi
		write_streams "$guest_bin"
		;;
	get)
		guest=${1:-}
		shift || true
		host=
		while [ $# -gt 0 ]; do
			case "$1" in
				--to)
					host=$2
					shift 2
					;;
				--timeout)
					shift 2
					;;
				*)
					shift
					;;
			esac
		done
		kind=$(kind_of "$guest")
		silent=${FAKE_GET_SILENT_SUCCESS:-}
		if [ -n "$silent" ] && { [ "$silent" = any ] || [ "$silent" = "$kind" ]; }; then
			exit 0
		fi
		fail=${FAKE_GET_FAIL:-}
		if [ -n "$fail" ] && { [ "$fail" = any ] || [ "$fail" = "$kind" ]; }; then
			if [ -f "$(gpath "$guest")" ]; then
				mkdir -p "$(dirname "$host")"
				if [ "${FAKE_GET_WRITE_THEN_FAIL:-0}" = 1 ]; then
					cp "$(gpath "$guest")" "$host"
				elif [ "${FAKE_GET_PARTIAL:-0}" = 1 ]; then
					head -c "${FAKE_GET_PARTIAL_BYTES:-1}" "$(gpath "$guest")" >"$host"
				fi
			fi
			exit 1
		fi
		[ -f "$(gpath "$guest")" ] || exit 1
		mkdir -p "$(dirname "$host")"
		cp "$(gpath "$guest")" "$host"
		;;
	*)
		exit 2
		;;
esac
