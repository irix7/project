#!/usr/bin/env python3
"""Host-only tests for scripts/rig/task-rig.sh (per-task rig instances).

Each case runs against a temporary IRIX_RIG_DIR with fake iris/iris-ci
binaries and a fake installed disk, so nothing touches the live rig, the
guest, nix, a real emulator or the network. The fake emulator records its
argv[0] and IRIS_COW_OVERLAY_DIR, which is how the process-title and
overlay-relocation contracts are proven without a guest.

Run: python3 scripts/rig/test-task-rig.py
"""

import os
import re
import signal
import socket
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RIG_DIR = REPO_ROOT / "scripts" / "rig"
TASK_RIG = RIG_DIR / "task-rig.sh"


class TaskRigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="task-rig-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        # A minimal base rig: fake emulator, fake iris-ci, media and a disk.
        release = self.root / "iris" / "target" / "release"
        release.mkdir(parents=True)
        self.iris = release / "iris"
        self.iris_ci = release / "iris-ci"
        (self.root / "media").mkdir()
        (self.root / "oracle").mkdir()
        disks = self.root / "disks"
        disks.mkdir()
        (disks / "irix65.raw").write_bytes(b"installed disk")

        self.record_file = self.root / "task.record"
        self.started = self.root / "started"

        self.write_fake_ic()
        self.write_fake_iris()

        self.env = dict(os.environ)
        self.env.update({
            "IRIX_RIG_DIR": str(self.root),
            "RIG_GUEST_LOCK_TIMEOUT": "10",
            "TASK_RECORD": str(self.record_file),
            "TASK_STARTED": str(self.started),
        })
        self.env.pop("RIG_GUEST_LOCK_HELD", None)

    def write_fake_ic(self):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -u
            cmd=
            skip=0
            for arg in "$@"; do
                if [ "$skip" = 1 ]; then skip=0; continue; fi
                case "$arg" in
                    --socket) skip=1 ;;
                    -*) ;;
                    *) [ -n "$cmd" ] || cmd=$arg ;;
                esac
            done
            case "$cmd" in
                ping)
                    python3 -c 'import os, socket, sys
try:
    s = socket.socket(socket.AF_UNIX)
    s.connect(os.environ["IRIS_SOCKET"])
except OSError:
    sys.exit(1)'
                    ;;
                quit) rm -f "$IRIS_SOCKET"; exit 0 ;;
                *) exit 0 ;;
            esac
            """
        )
        self.iris_ci.write_text(script)
        self.iris_ci.chmod(0o755)

    def write_fake_iris(self):
        # The fake stays a bash process (it backgrounds the socket binder and
        # waits) so its /proc cmdline keeps this task's --config path, which is
        # what task-rig's running-check reads. A real iris is a single ELF with
        # the same cmdline.
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            {
                printf 'argv0: %s\\n' "$0"
                printf 'title: %s\\n' "${RIG_PROC_TITLE:-}"
                printf 'overlay: %s\\n' "${IRIS_COW_OVERLAY_DIR:-}"
                printf 'socket: %s\\n' "${RIG_SOCKET:-}"
            } >> "$TASK_RECORD"
            printf '%s\\n' "$$" >> "$TASK_STARTED"
            python3 -c 'import os, socket, time
            try:
                os.unlink(os.environ["RIG_SOCKET"])
            except FileNotFoundError:
                pass
            s = socket.socket(socket.AF_UNIX)
            s.bind(os.environ["RIG_SOCKET"])
            s.listen(1)
            time.sleep(300)' &
            wait
            """
        )
        self.iris.write_text(script)
        self.iris.chmod(0o755)

    def run_task_rig(self, *args, timeout=30):
        return subprocess.run(
            ["bash", str(TASK_RIG), *args],
            env=self.env, capture_output=True, text=True, timeout=timeout,
        )

    def kill_recorded_emulators(self):
        if not self.started.exists():
            return
        for line in self.started.read_text().split():
            try:
                os.killpg(int(line), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

    def bind_base_socket(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.root / "iris.sock"))
        sock.listen(1)
        self.addCleanup(sock.close)

    def leave_stale_base_socket(self):
        # A crashed base leaves the socket file behind with nothing listening;
        # the OS keeps the bound path after close, so a connect is refused.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.root / "iris.sock"))
        sock.close()
        (self.root / "iris.pid").write_text("999999\n")

    def record(self):
        fields = {}
        if self.record_file.exists():
            for line in self.record_file.read_text().splitlines():
                key, _, value = line.partition(": ")
                fields[key] = value
        return fields


class BasicCommandsTest(TaskRigTestCase):
    def test_list_with_no_instances(self):
        result = self.run_task_rig("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no task instances", result.stdout)

    def test_path_slugifies_the_task(self):
        result = self.run_task_rig("path", "a b/c")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), str(self.root / "tasks" / "a-b-c")
        )

    def test_env_prints_the_instance_rig_dir(self):
        root = self.root / "tasks" / "eoe.sw.base"
        root.mkdir(parents=True)
        result = self.run_task_rig("env", "eoe.sw.base")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export IRIX_RIG_DIR=", result.stdout)
        self.assertIn(str(root), result.stdout)

    def test_run_sets_the_instance_rig_dir(self):
        root = self.root / "tasks" / "eoe.sw.base"
        root.mkdir(parents=True)
        result = self.run_task_rig(
            "run", "eoe.sw.base", "bash", "-c", 'printf %s "$IRIX_RIG_DIR"'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(root))


class StartTest(TaskRigTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.kill_recorded_emulators)

    def test_start_creates_a_named_instance(self):
        result = self.run_task_rig("start", "eoe.sw.base")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        root = self.root / "tasks" / "eoe.sw.base"
        self.assertTrue((root / "iris.toml").is_file())
        self.assertTrue((root / "work").is_dir())

        config = (root / "iris.toml").read_text()
        self.assertIn("overlay = true", config)
        self.assertIn(f'ci_socket  = "{root}/iris.sock"', config)

        # The emulator was told to keep its overlay with the task, and to title
        # itself for the task so `ps` shows what each instance belongs to.
        # (argv[0] itself only survives on a real ELF; a script fake loses it
        # to the shebang, so the title is asserted through the env start-rig
        # hands the emulator.)
        rec = self.record()
        self.assertEqual(rec["title"], "iris2[eoe.sw.base]")
        self.assertEqual(rec["overlay"], str(root / "disks"))
        self.assertEqual(rec["socket"], str(root / "iris.sock"))

        status = self.run_task_rig("status", "eoe.sw.base")
        self.assertIn("running", status.stdout)

    def test_start_refuses_while_base_rig_runs(self):
        self.bind_base_socket()
        result = self.run_task_rig("start", "eoe.sw.base")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base rig is running", result.stderr)

    def test_start_override_allows_a_running_base_rig(self):
        self.bind_base_socket()
        self.env["IRIX_TASK_ALLOW_BASE_RUNNING"] = "1"
        result = self.run_task_rig("start", "eoe.sw.base")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stale_base_socket_does_not_block_start(self):
        # All three rebuild workers hit this: a crashed base leaves a socket
        # file and a dead pid, and the old check refused every task start.
        self.leave_stale_base_socket()
        result = self.run_task_rig("start", "eoe.sw.base")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_task_limit_is_enforced(self):
        self.env["IRIX_TASK_MAX"] = "1"
        self.assertEqual(self.run_task_rig("start", "a.one").returncode, 0)
        result = self.run_task_rig("start", "b.two")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("task limit", result.stderr)

    def test_start_without_an_installed_disk_is_refused(self):
        (self.root / "disks" / "irix65.raw").unlink()
        result = self.run_task_rig("start", "eoe.sw.base")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no installed disk", result.stderr)

    def test_start_seeds_nvram_from_the_base_rig(self):
        (self.root / "nvram.bin").write_bytes(b"seeded-prom")
        result = self.run_task_rig("start", "eoe.sw.base")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        root = self.root / "tasks" / "eoe.sw.base"
        self.assertEqual((root / "nvram.bin").read_bytes(), b"seeded-prom")

    def test_start_does_not_clobber_an_instance_nvram(self):
        (self.root / "nvram.bin").write_bytes(b"seeded-prom")
        self.assertEqual(self.run_task_rig("start", "eoe.sw.base").returncode, 0)
        root = self.root / "tasks" / "eoe.sw.base"
        (root / "nvram.bin").write_bytes(b"mine")
        self.assertEqual(
            self.run_task_rig("stop", "eoe.sw.base", "--timeout", "2").returncode, 0
        )
        self.assertEqual(self.run_task_rig("start", "eoe.sw.base").returncode, 0)
        self.assertEqual((root / "nvram.bin").read_bytes(), b"mine")


class StopTest(TaskRigTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.kill_recorded_emulators)

    def test_stop_rm_discards_the_overlay(self):
        start = self.run_task_rig("start", "eoe.sw.base")
        self.assertEqual(start.returncode, 0, start.stdout + start.stderr)
        root = self.root / "tasks" / "eoe.sw.base"
        overlay = root / "disks" / "scsi1.overlay"
        overlay.write_bytes(b"dirty")
        (root / "disks" / "scsi1.overlay.dirty").write_bytes(b"sidecar")

        result = self.run_task_rig("stop", "eoe.sw.base", "--rm", "--timeout", "2")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(overlay.exists())
        self.assertFalse((root / "disks" / "scsi1.overlay.dirty").exists())

    def test_stop_without_rm_keeps_the_overlay(self):
        start = self.run_task_rig("start", "eoe.sw.base")
        self.assertEqual(start.returncode, 0, start.stdout + start.stderr)
        root = self.root / "tasks" / "eoe.sw.base"
        overlay = root / "disks" / "scsi1.overlay"
        overlay.write_bytes(b"dirty")

        result = self.run_task_rig("stop", "eoe.sw.base", "--timeout", "2")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(overlay.exists())


class ListTest(TaskRigTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.kill_recorded_emulators)

    def test_list_shows_running_and_stopped_instances(self):
        self.assertEqual(self.run_task_rig("start", "a.one").returncode, 0)
        self.assertEqual(self.run_task_rig("start", "b.two").returncode, 0)
        self.assertEqual(
            self.run_task_rig("stop", "b.two", "--timeout", "2").returncode, 0
        )

        result = self.run_task_rig("list")

        lines = [l for l in result.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2, result.stdout)
        text = result.stdout
        self.assertIn("a.one", text)
        self.assertIn("b.two", text)
        self.assertIn("running", text)

    def test_instances_get_distinct_monitor_and_serial_ports(self):
        # Parallel instances must not fight over the monitor/serial ports.
        self.assertEqual(self.run_task_rig("start", "a.one").returncode, 0)
        self.assertEqual(self.run_task_rig("start", "b.two").returncode, 0)
        ports = []
        for task in ("a.one", "b.two"):
            config = (self.root / "tasks" / task / "iris.toml").read_text()
            m = re.search(r"^monitor_port\s*=\s*(\d+)", config, re.M)
            self.assertIsNotNone(m, config)
            ports.append(m.group(1))
        self.assertNotEqual(ports[0], ports[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
