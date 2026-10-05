#!/usr/bin/env python3
"""Host-only fault-injection tests for the rig's lifecycle scripts (issue #24).

Every case runs against a temporary IRIX_RIG_DIR with fake iris/iris-ci
binaries, a mocked stop script and a real Unix socket under the temp root.
Nothing touches the live rig, the sibling session, nix or the network, and
every wait is bounded. The live-guest lifecycle test is deliberately not run
here; these cases prove the socket-identity, process-ownership, fresh-reset
and lock invariants before anyone drives a controlled guest.

Run: python3 scripts/rig/test-rig-lifecycle.py
"""

import os
import shutil
import signal
import socket
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RIG_DIR = REPO_ROOT / "scripts" / "rig"
LIB = RIG_DIR / "lib.sh"
START = RIG_DIR / "start-rig.sh"
STOP = RIG_DIR / "stop-rig.sh"
PROVISION = RIG_DIR / "provision-guest.sh"
ORACLE = RIG_DIR / "oracle.sh"


class RigTestCase(unittest.TestCase):
    """A temp rig tree, fake binaries and an environment pinned to the temp root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rig-lifecycle-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.iris_dir = self.root / "iris" / "target" / "release"
        self.iris_dir.mkdir(parents=True)
        self.iris = self.iris_dir / "iris"
        self.iris_ci = self.iris_dir / "iris-ci"

        self.config = self.root / "iris.toml"
        self.config.write_text("headless = false\n")
        self.socket = self.root / "iris.sock"
        self.pid_file = self.root / "iris.pid"

        self.env = dict(os.environ)
        self.env.update({
            "IRIX_RIG_DIR": str(self.root),
            "IRIS_SOCKET": "/tmp/iris.sock",
            "RIG_GUEST_LOCK_TIMEOUT": "10",
        })
        self.env.pop("RIG_GUEST_LOCK_HELD", None)
        self.env.pop("FAKE_IC_RECORD", None)

    def bash(self, script, timeout=30):
        return subprocess.run(
            ["bash", "-c", script],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def bind_socket(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket))
        self.addCleanup(sock.close)
        return sock

    def write_fake_ic(self, quit_removes_socket=True):
        """A fake iris-ci: records argv/env, answers ping, may remove the socket on quit."""
        quit_body = 'rm -f "$IRIS_SOCKET"; exit 0'
        if not quit_removes_socket:
            quit_body = "exit 0"
        script = textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -u
            rec=${{FAKE_IC_RECORD:-/dev/null}}
            printf 'argv: %s\\n' "$*" >> "$rec"
            printf 'env: %s\\n' "$IRIS_SOCKET" >> "$rec"
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
                ping) [ -S "$IRIS_SOCKET" ] ;;
                quit) {quit_body} ;;
                *) exit 0 ;;
            esac
            """
        )
        self.iris_ci.write_text(script)
        self.iris_ci.chmod(0o755)

    def write_fake_iris(self):
        """A fake emulator: records its pid, binds the rig socket, then sleeps."""
        script = textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$$" >> "{self.root}/started"
            exec python3 -c 'import os, socket, time
try:
    os.unlink(os.environ["RIG_SOCKET"])
except FileNotFoundError:
    pass
s = socket.socket(socket.AF_UNIX)
s.bind(os.environ["RIG_SOCKET"])
s.listen(1)
time.sleep(300)'
            """
        )
        self.iris.write_text(script)
        self.iris.chmod(0o755)

    def write_fake_stop(self, exit_code=0):
        stop = self.root / "fake-stop.sh"
        stop.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n")
        stop.chmod(0o755)
        return stop

    def kill_recorded_emulators(self):
        started = self.root / "started"
        if not started.exists():
            return
        for line in started.read_text().split():
            try:
                os.killpg(int(line), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

    def shaped_process(self, argv):
        """A controlled process that stays alive with an exact argv until killed."""
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(proc.stdin.close)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def shaped_emulator(self):
        """A controlled process whose /proc exe and cmdline match the rig's iris.

        A copy of bash is the rig binary and reads a pipe, so it stays alive
        with the rig's --config and --ci in its argv until the test kills it.
        """
        shutil.copy(shutil.which("bash"), self.iris)
        self.iris.chmod(0o755)
        return self.shaped_process(
            [
                str(self.iris),
                "-c",
                'read -r -d "" x',
                "--config",
                str(self.config),
                "--ci",
            ]
        )

    def run_script(self, script, *args, timeout=30):
        return subprocess.run(
            ["bash", str(script), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


class PidOwnershipTest(RigTestCase):
    def pid_is_ours(self, pid):
        result = self.bash(f'source "{LIB}"; rig_pid_is_ours {pid}')
        return result.returncode == 0, result

    def test_unrelated_sleep_is_not_ours(self):
        sleeper = subprocess.Popen(["sleep", "60"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        ours, _ = self.pid_is_ours(sleeper.pid)
        self.assertFalse(ours)

    def test_missing_process_is_not_ours(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        if os.path.exists(f"/proc/{proc.pid}"):
            self.skipTest("pid recycled while the test ran")
        ours, _ = self.pid_is_ours(proc.pid)
        self.assertFalse(ours)

    def test_non_numeric_pid_is_not_ours(self):
        ours, _ = self.pid_is_ours("not-a-pid")
        self.assertFalse(ours)

    def test_shaped_emulator_with_rig_argv_is_ours(self):
        proc = self.shaped_emulator()
        ours, _ = self.pid_is_ours(proc.pid)
        self.assertTrue(ours)

    def test_rig_binary_with_another_config_is_not_ours(self):
        shutil.copy(shutil.which("bash"), self.iris)
        self.iris.chmod(0o755)
        other = self.shaped_process(
            [
                str(self.iris),
                "-c",
                'read -r -d "" x',
                "--config",
                str(self.root / "other.toml"),
                "--ci",
            ]
        )
        ours, _ = self.pid_is_ours(other.pid)
        self.assertFalse(ours)

    def test_rig_binary_without_ci_is_not_ours(self):
        shutil.copy(shutil.which("bash"), self.iris)
        self.iris.chmod(0o755)
        proc = self.shaped_process(
            [str(self.iris), "-c", 'read -r -d "" x', "--config", str(self.config)]
        )
        ours, _ = self.pid_is_ours(proc.pid)
        self.assertFalse(ours)


class StopRigTest(RigTestCase):
    def test_stale_pid_is_refused_and_never_signalled(self):
        self.bind_socket()
        sleeper = subprocess.Popen(["sleep", "60"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.pid_file.write_text(f"{sleeper.pid}\n")
        self.write_fake_ic(quit_removes_socket=False)

        result = self.run_script(STOP, "--timeout", "1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(sleeper.poll(), "the unrelated process was signalled")
        self.assertTrue(self.socket.exists(), "the socket was removed")
        self.assertTrue(self.pid_file.exists(), "the stale pid file was removed")
        self.assertIn("refusing", result.stdout + result.stderr)

    def test_verified_pid_is_terminated_and_stale_socket_removed(self):
        self.bind_socket()
        proc = self.shaped_emulator()
        self.pid_file.write_text(f"{proc.pid}\n")
        self.write_fake_ic(quit_removes_socket=False)

        result = self.run_script(STOP, "--timeout", "1")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNotNone(proc.poll(), "the verified emulator survived")
        self.assertFalse(self.socket.exists(), "the stale socket was left behind")
        self.assertFalse(self.pid_file.exists(), "the pid file was left behind")
        self.assertIn("SIGTERM", result.stdout)

    def test_clean_quit_removes_socket_and_pid_file(self):
        self.bind_socket()
        self.write_fake_ic(quit_removes_socket=True)

        result = self.run_script(STOP, "--timeout", "5")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.socket.exists())
        self.assertFalse(self.pid_file.exists())
        self.assertIn("stopped", result.stdout)


class FreshResetTest(RigTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "phase-a.done").write_text("marker\n")
        self.disk = self.root / "disks" / "irix65.raw"
        self.disk.parent.mkdir()
        self.disk.write_bytes(b"disk")
        self.nvram = self.root / "nvram.bin"
        self.nvram.write_bytes(b"nvram")

    def reset(self, stop):
        self.env["RIG_STOP_SCRIPT"] = str(stop)
        return self.bash(f'source "{LIB}"; rig_fresh_reset')

    def assert_untouched(self):
        self.assertTrue((self.state / "phase-a.done").exists())
        self.assertTrue(self.disk.exists())
        self.assertTrue(self.nvram.exists())

    def test_clean_state_is_deleted(self):
        result = self.reset(self.write_fake_stop(exit_code=0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.disk.exists())
        self.assertFalse(self.nvram.exists())

    def test_present_socket_deletes_nothing(self):
        self.bind_socket()
        result = self.reset(self.write_fake_stop(exit_code=0))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("socket", result.stderr)
        self.assert_untouched()

    def test_live_pid_deletes_nothing(self):
        sleeper = subprocess.Popen(["sleep", "60"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.pid_file.write_text(f"{sleeper.pid}\n")
        result = self.reset(self.write_fake_stop(exit_code=0))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alive", result.stderr)
        self.assert_untouched()

    def test_failing_stop_still_deletes_nothing(self):
        self.bind_socket()
        result = self.reset(self.write_fake_stop(exit_code=1))
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()


class GuestLockTest(RigTestCase):
    def test_nested_lock_is_reentrant(self):
        result = self.bash(
            f'source "{LIB}"; rig_with_guest_lock rig_with_guest_lock true',
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_concurrent_holders_serialise(self):
        marker = self.root / "critical"
        script = textwrap.dedent(
            f"""\
            source "{LIB}"
            rig_with_guest_lock bash -c '
                echo start >> "{marker}"
                sleep 0.3
                echo end >> "{marker}"
            '
            """
        )
        procs = [
            subprocess.Popen(
                ["bash", "-c", script], env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        for proc in procs:
            _, stderr = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(marker.read_text().split(), ["start", "end", "start", "end"])

    def test_lock_timeout_is_bounded(self):
        self.env["RIG_GUEST_LOCK_TIMEOUT"] = "1"
        ready = self.root / "ready"
        holder = subprocess.Popen(
            ["flock", str(self.root / "guest.lock"),
             "bash", "-c", f"touch {ready}; sleep 5"]
        )
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.1)
        self.assertTrue(ready.exists(), "lock holder did not start")

        started = time.monotonic()
        result = self.bash(f'source "{LIB}"; rig_with_guest_lock true', timeout=10)
        elapsed = time.monotonic() - started

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed out", result.stderr)
        self.assertLess(elapsed, 5)

    def test_start_waits_for_another_holder_then_refuses_on_timeout(self):
        self.addCleanup(self.kill_recorded_emulators)
        self.write_fake_iris()
        self.write_fake_ic()
        self.env["RIG_GUEST_LOCK_TIMEOUT"] = "1"
        holder = subprocess.Popen(["flock", str(self.root / "guest.lock"), "sleep", "5"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        time.sleep(0.3)

        result = self.run_script(START)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed out", result.stderr)
        self.assertFalse((self.root / "started").exists())

    def test_lifecycle_clients_take_the_lock(self):
        self.env["RIG_GUEST_LOCK_TIMEOUT"] = "1"
        holder = subprocess.Popen(
            ["flock", str(self.root / "guest.lock"), "sleep", "5"]
        )
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        time.sleep(0.3)

        cases = [
            (STOP, ["--timeout", "1"]),
            (PROVISION, []),
            (ORACLE, ["status"]),
        ]
        for script, args in cases:
            with self.subTest(script=script.name):
                result = self.run_script(script, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("timed out", result.stderr)

    def test_concurrent_starts_yield_one_instance(self):
        self.addCleanup(self.kill_recorded_emulators)
        self.write_fake_iris()
        self.write_fake_ic()
        self.env["RIG_GUEST_LOCK_TIMEOUT"] = "20"
        procs = [
            subprocess.Popen(
                ["bash", str(START)], env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        for proc in procs:
            _, stderr = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, stderr)

        started = (self.root / "started").read_text().split()
        self.assertEqual(len(started), 1, f"expected one emulator, got {started}")

        pid = int(self.pid_file.read_text())
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        (self.root / "started").write_text("")


class IcSocketTest(RigTestCase):
    def test_ic_carries_the_selected_socket_in_argv_and_env(self):
        self.bind_socket()
        self.write_fake_ic()
        record = self.root / "ic.record"
        self.env["FAKE_IC_RECORD"] = str(record)

        result = self.bash(f'source "{LIB}"; ic ping')

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            record.read_text().splitlines(),
            [f"argv: --socket {self.socket} ping", f"env: {self.socket}"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
