#!/usr/bin/env bash
#
# Per-task rig instances for parallel rebuild agents.
#
# One agent rebuilds one board item; this puts a private emulator beside it,
# named for that item. An instance is a directory under $IRIX_RIG_DIR/tasks:
# its own socket, guest lock, NVRAM, scratch LUN, serial log and COW overlay,
# so any number can run at once without collision. The installed disk is
# shared read-only and every write lands in the instance's own overlay.
#
# The instance lazy-loads the shared rig: the emulator, media, oracle and the
# installed disk are symlinked from the base rig, never copied. The installed
# disk must be quiescent — the base rig must be stopped — while tasks overlay
# it, or a write under a running base rig would corrupt the shared base. Set
# IRIX_TASK_ALLOW_BASE_RUNNING=1 to override (only when the base is known cold).
#
# The emulator sees IRIS_COW_OVERLAY_DIR=<instance>/disks (the fork honours
# it), so the overlay stays on the shared volume and is named for the task
# rather than a pid. Start also sets the process title to iris2[<task>], so
# `ps aux` shows which task each running emulator belongs to.
#
# Usage:
#   scripts/rig/task-rig.sh start <task> [--gui]
#   scripts/rig/task-rig.sh stop  <task> [--rm] [--timeout SECONDS]
#   scripts/rig/task-rig.sh status <task>
#   scripts/rig/task-rig.sh list
#   scripts/rig/task-rig.sh path <task>
#
# <task> is the board item (the command or program being rebuilt), e.g.
# eoe.sw.base. It is slugified for the directory name.
#
set -euo pipefail

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/rig/lib.sh
source "$SELF_DIR/lib.sh"

BASE=$RIG_DIR
TASKS_DIR=$BASE/tasks
TITLE_PREFIX=${RIG_TASK_TITLE_PREFIX:-iris2}

usage() {
	cat <<'EOF'
Usage:
  scripts/rig/task-rig.sh start <task> [--gui]
  scripts/rig/task-rig.sh stop  <task> [--rm] [--timeout SECONDS]
  scripts/rig/task-rig.sh status <task>
  scripts/rig/task-rig.sh list
  scripts/rig/task-rig.sh path <task>
  scripts/rig/task-rig.sh env  <task>          # print IRIX_RIG_DIR for this instance
  scripts/rig/task-rig.sh run  <task> <cmd...> # run a command against this instance

  start   bring up this task's emulator (private socket/lock/overlay)
  --gui   map the graphics board (default: headless, no window)
  stop    stop the emulator; --rm also discards the disk overlay
  status  show one instance
  list    show every instance
  path    print the instance directory
  env     print `export IRIX_RIG_DIR=...` for this instance (eval it, then the
          shared scripts address this task, not the base rig)
  run     run a command with IRIX_RIG_DIR pointed at this instance

  A cap of IRIX_TASK_MAX (default 2) running instances is enforced, because each
  emulator pins a core. Raise it only if the host can take it.

<task> is the board item being rebuilt (e.g. eoe.sw.base).
EOF
}

slugify() {
	# Filename-safe, path-safe: anything outside [A-Za-z0-9._-] becomes '-'.
	printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '-'
}

task_root() {
	printf '%s/%s\n' "$TASKS_DIR" "$(slugify "$1")"
}

base_disk() {
	printf '%s/disks/irix65.raw\n' "$BASE"
}

base_running() {
	# Liveness, not mere existence: a crashed base can leave a stale socket
	# file and a dead pid behind. A socket only counts if it answers a ping;
	# a pid only counts if it is verifiably this rig's emulator.
	rig_running && return 0
	local pid=
	[ -f "$RIG_PID_FILE" ] && pid=$(cat "$RIG_PID_FILE" 2>/dev/null || true)
	[ -n "$pid" ] && rig_pid_is_ours "$pid"
}

task_pid() {
	# Print the running emulator's pid for this instance, or nothing.
	local root=$1 pid pid_file="$1/iris.pid"
	[ -f "$pid_file" ] || return 1
	pid=$(cat "$pid_file" 2>/dev/null || true)
	[ -n "$pid" ] || return 1
	rig_pid_alive "$pid" || return 1
	# Confirm it is this task's emulator, not a recycled pid.
	grep -qaF -- "$root/iris.toml" "/proc/$pid/cmdline" 2>/dev/null || return 1
	printf '%s\n' "$pid"
}

# Number of task instances currently running (the base rig is not counted).
running_task_count() {
	local root count=0
	shopt -s nullglob
	for root in "$TASKS_DIR"/*/; do
		root=${root%/}
		task_pid "$root" >/dev/null 2>&1 && count=$((count + 1))
	done
	printf '%s\n' "$count"
}

link_shared() {
	# link_shared TARGET LINK: keep LINK a symlink to TARGET when TARGET exists.
	local target=$1 link=$2
	[ -e "$target" ] || return 0
	if [ -L "$link" ] && [ "$(readlink "$link")" = "$target" ]; then
		return 0
	fi
	rm -f "$link"
	ln -s "$target" "$link"
}

write_config() {
	local root=$1 headless=$2 sum mac port_base
	# A distinct MAC and monitor/serial ports per task. The base rig owns
	# 08:00:69:57:07:01 and the default ports (8888/8880/8881); without our
	# own, parallel instances would fight over the monitor and serial ports.
	sum=$(printf '%s' "$root" | cksum | cut -d' ' -f1)
	mac=$(printf '08:00:69:57:%02x:%02x' $(((sum >> 8) & 0xff)) $((sum & 0xff)))
	port_base=$(( 20000 + (sum % 1000) * 3 ))
	cat >"$root/iris.toml" <<EOF
# Per-task IRIX 6.5.7m instance (scripts/rig/task-rig.sh). Generated; edit
# task-rig.sh, not this file, when the shape must change.

headless   = $headless
no_audio   = true
banks      = [128, 128, 0, 0]
ci_socket  = "$root/iris.sock"
nvram      = "$root/nvram.bin"
serial_log = "$root/logs/serial.log"

monitor_port  = $port_base
serial_port_a = $((port_base + 1))
serial_port_b = $((port_base + 2))

[machine]
cpu = "r4400"

[scsi.1]
path    = "$root/disks/irix65.raw"
cdrom   = false
overlay = true

[scsi.2]
path     = "$root/scratch.raw"
scratch  = true
size_mb  = 64

[scsi.4]
path = "$RIG_MEDIA_DIR/${RIG_MEDIA_TOOLS_OVERLAYS_1}"
cdrom = true
discs = [
  "$RIG_MEDIA_DIR/${RIG_MEDIA_TOOLS_OVERLAYS_1}",
  "$RIG_MEDIA_DIR/${RIG_MEDIA_FOUNDATION_1}",
  "$RIG_MEDIA_DIR/${RIG_MEDIA_FOUNDATION_2}",
  "$RIG_MEDIA_DIR/${RIG_MEDIA_OVERLAYS_2}",
]

[network]
mac = "$mac"
EOF
}

seed_nvram() {
	# The base rig's NVRAM holds the seeded PROM environment (SystemPartition,
	# OSLoadPartition, console=d). A fresh instance with no NVRAM reinitialises
	# and drops to the maintenance menu, so copy the base's once; the instance
	# then owns and mutates its own copy. A pre-existing instance NVRAM is left
	# alone.
	local root=$1 base_nvram="$BASE/nvram.bin"
	[ -f "$root/nvram.bin" ] && return 0
	if [ ! -f "$base_nvram" ]; then
		rig_log "warning: no base NVRAM at $base_nvram; ${root##*/} will reinitialise"
		return 0
	fi
	cp "$base_nvram" "$root/nvram.bin"
	rig_log "seeded NVRAM from base for ${root##*/}"
}

ensure_instance() {
	local root=$1 headless=$2
	local disk
	disk=$(base_disk)
	[ -f "$disk" ] || rig_die "no installed disk at $disk (provision the base rig first)"

	mkdir -p "$root/disks" "$root/logs" "$root/work" "$root/state"
	link_shared "$RIG_IRIS_DIR" "$root/iris"
	link_shared "$RIG_MEDIA_DIR" "$root/media"
	link_shared "$RIG_ORACLE_DIR" "$root/oracle"
	link_shared "$disk" "$root/disks/irix65.raw"
	seed_nvram "$root"

	[ -f "$root/iris.toml" ] || write_config "$root" "$headless"
}

cmd_start() {
	local task=$1 gui=$2
	local root
	root=$(task_root "$task")

	if base_running && [ -z "${IRIX_TASK_ALLOW_BASE_RUNNING:-}" ]; then
		rig_die "the base rig is running; its installed disk is this task's shared base. Stop it first, or set IRIX_TASK_ALLOW_BASE_RUNNING=1 if it is known cold."
	fi

	local headless=true
	[ "$gui" = 1 ] && headless=false
	ensure_instance "$root" "$headless"

	if task_pid "$root" >/dev/null; then
		rig_log "already running: $task ($root)"
		return 0
	fi

	local max=${IRIX_TASK_MAX:-2} running
	running=$(running_task_count)
	if [ "$running" -ge "$max" ]; then
		rig_die "task limit reached ($running running, IRIX_TASK_MAX=$max); stop one with 'task-rig.sh stop <task>', or raise IRIX_TASK_MAX if the host can take it"
	fi

	rig_log "starting task $task ($root)"
	IRIX_RIG_DIR="$root" \
		RIG_PROC_TITLE="$TITLE_PREFIX[$task]" \
		IRIS_COW_OVERLAY_DIR="$root/disks" \
		"$SELF_DIR/start-rig.sh"
	rig_log "task $task up; socket $root/iris.sock, work $root/work"
}

cmd_stop() {
	local task=$1 rm=$2 timeout=$3
	local root
	root=$(task_root "$task")
	[ -d "$root" ] || rig_die "no instance for task $task ($root)"

	local -a args=()
	[ -n "$timeout" ] && args+=(--timeout "$timeout")
	IRIX_RIG_DIR="$root" "$SELF_DIR/stop-rig.sh" "${args[@]}"

	if [ "$rm" = 1 ]; then
		rig_log "discarding overlay for $task"
		rm -f "$root/disks/scsi1.overlay" "$root/disks/scsi1.overlay.dirty" \
			"$root/disks/irix65.raw.overlay" "$root/disks/irix65.raw.overlay.dirty"
	fi
	rig_log "task $task stopped"
}

# Print the exports that point the shared scripts at this instance. Workers
# eval this instead of hand-setting IRIX_RIG_DIR for every command:
#   eval "$(scripts/rig/task-rig.sh env <task>)"
#   scripts/rig/oracle-driver.py ensure-shell --socket "$RIG_SOCKET"
cmd_env() {
	local task=$1 root
	[ -n "$task" ] || rig_die "env needs a task name"
	root=$(task_root "$task")
	[ -d "$root" ] || rig_die "no instance for task $task ($root); start it first"
	printf 'export IRIX_RIG_DIR=%q\n' "$root"
}

# Run a command with IRIX_RIG_DIR pointed at this instance, so the shared rig
# scripts address this task. (nix develop may drop the variable; pass
# `--keep IRIX_RIG_DIR` when the command goes through it.)
cmd_run() {
	local task=$1
	shift || true
	[ -n "$task" ] || rig_die "run needs a task name"
	[ "$#" -gt 0 ] || rig_die "run needs a command"
	local root
	root=$(task_root "$task")
	[ -d "$root" ] || rig_die "no instance for task $task ($root); start it first"
	IRIX_RIG_DIR="$root" "$@"
}

render_instance() {
	local root=$1 task=${1##*/} pid overlay line
	pid=$(task_pid "$root" 2>/dev/null || true)
	overlay=$(du -h "$root/disks/scsi1.overlay" 2>/dev/null | cut -f1 || true)
	line=$(printf '%-28s %-10s %s' "$task" "${pid:+running $pid}" "${overlay:+overlay $overlay}")
	printf '%s\n' "$line"
}

cmd_status() {
	local root
	root=$(task_root "$1")
	[ -d "$root" ] || rig_die "no instance for task $1 ($root)"
	printf 'task     %s\n' "${root##*/}"
	printf 'root     %s\n' "$root"
	local pid
	if pid=$(task_pid "$root"); then
		printf 'state    running (pid %s)\n' "$pid"
	else
		printf 'state    stopped\n'
	fi
	printf 'socket   %s%s\n' "$root/iris.sock" \
		"$([ -S "$root/iris.sock" ] && printf ' (present)' || printf ' (absent)')"
	printf 'config   %s\n' "$root/iris.toml"
	printf 'overlay  %s\n' "$(du -h "$root/disks/scsi1.overlay" 2>/dev/null | cut -f1 || printf 'none')"
	printf 'work     %s\n' "$root/work"
}

cmd_list() {
	local found=0 root
	shopt -s nullglob
	for root in "$TASKS_DIR"/*/; do
		root=${root%/}
		found=1
		render_instance "$root"
	done
	[ "$found" = 1 ] || printf '(no task instances under %s)\n' "$TASKS_DIR"
}

main() {
	local action=${1:-}
	[ -n "$action" ] || { usage >&2; exit 2; }
	shift

	local task= rm=0 gui=0 timeout=
	case "$action" in
		start)
			task=${1:-}; shift || true
			[ -n "$task" ] || rig_die "start needs a task name"
			while [ $# -gt 0 ]; do
				case "$1" in
					--gui) gui=1; shift ;;
					-h|--help) usage; exit 0 ;;
					*) rig_die "unknown option: $1 (try --help)" ;;
				esac
			done
			cmd_start "$task" "$gui"
			;;
		stop)
			task=${1:-}; shift || true
			[ -n "$task" ] || rig_die "stop needs a task name"
			while [ $# -gt 0 ]; do
				case "$1" in
					--rm) rm=1; shift ;;
					--timeout) timeout=$2; shift 2 ;;
					-h|--help) usage; exit 0 ;;
					*) rig_die "unknown option: $1 (try --help)" ;;
				esac
			done
			cmd_stop "$task" "$rm" "$timeout"
			;;
		status)
			[ -n "${1:-}" ] || rig_die "status needs a task name"
			cmd_status "$1"
			;;
		list)
			cmd_list
			;;
		path)
			[ -n "${1:-}" ] || rig_die "path needs a task name"
			task_root "$1"
			;;
		env)
			[ -n "${1:-}" ] || rig_die "env needs a task name"
			cmd_env "$1"
			;;
		run)
			cmd_run "$@"
			;;
		-h|--help|help)
			usage
			;;
		*)
			rig_die "unknown action: $action (try --help)"
			;;
	esac
}

main "$@"
