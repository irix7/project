#!/usr/bin/env python3
"""Unit and fault-injection tests for the smoke harness (issue #5).

The compile/ship/run/diff path against the live guest is the integrator's
controlled rerun; everything here is host-only. Two seams are exercised:

  * scripts/smoke.sh end to end against a fake cross prefix and a temporary
    IRIX_RIG_DIR whose iris-ci is the committed fake (scripts/rig/
    fake-iris-ci.sh). Each fault the acceptance boundary names -- failed put,
    run or get, timeout, missing or invalid status, partial output, stale
    matching evidence, non-zero programme exit, output mismatch, readelf
    failure -- must fail the harness;
  * scripts/lib/guest-txn.sh directly, so the reusable fail-closed contract
    and its exit statuses are pinned independently of smoke.sh.

Nothing here touches the live rig, the guest, nix, a real compiler, an
emulator or the network.
"""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE = ROOT / "scripts" / "smoke.sh"
TXN = ROOT / "scripts" / "lib" / "guest-txn.sh"
SOURCE = ROOT / "oracle" / "hello.c"
EXPECTED = ROOT / "scripts" / "smoke" / "hello.expected"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fake_rig = _load("fake_rig", ROOT / "scripts" / "rig" / "fake_rig.py")


def run_smoke(*args, **kwargs):
    return subprocess.run(
        ["bash", str(SMOKE), *args],
        capture_output=True,
        text=True,
        **kwargs,
    )


class Help(unittest.TestCase):
    def test_help_prints_usage_and_flags(self):
        proc = run_smoke("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Usage: scripts/smoke.sh", proc.stdout)
        for flag in ("--prefix", "--sysroot", "--abi", "--cflags", "--timeout"):
            self.assertIn(flag, proc.stdout)


class ArgumentValidation(unittest.TestCase):
    def test_unknown_option(self):
        proc = run_smoke("--nope", str(SOURCE), str(EXPECTED))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unknown option", proc.stderr)

    def test_missing_operands(self):
        proc = run_smoke()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SOURCE and EXPECTED are required", proc.stderr)

    def test_extra_operand(self):
        proc = run_smoke(str(SOURCE), str(EXPECTED), "third")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unexpected extra argument", proc.stderr)

    def test_unknown_abi_fails_before_touching_the_rig(self):
        proc = run_smoke("--abi", "nope", str(SOURCE), str(EXPECTED))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unknown ABI", proc.stderr)

    def test_missing_source(self):
        proc = run_smoke(str(ROOT / "oracle" / "not-there.c"), str(EXPECTED))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("source not found", proc.stderr)

    def test_missing_expected(self):
        proc = run_smoke(str(SOURCE), str(ROOT / "smoke" / "not-there.expected"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("expected output not found", proc.stderr)


class LocalPreconditions(unittest.TestCase):
    def test_missing_prefix(self):
        proc = run_smoke("--prefix", "/nonexistent/prefix", str(SOURCE), str(EXPECTED))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("toolchain prefix not found", proc.stderr)

    def test_prefix_without_compiler(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bin").mkdir()
            proc = run_smoke("--prefix", tmp, str(SOURCE), str(EXPECTED))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("cross compiler not found", proc.stderr)


class FaultInjectionBase(unittest.TestCase):
    """A temp rig plus a fake cross prefix, shared by the smoke e2e cases."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="smoke-faults-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.rig = fake_rig.FakeRig(root / "rig")
        self.sysroot = root / "sysroot"
        (self.sysroot / "usr" / "include").mkdir(parents=True)
        self.prefix = fake_rig.write_fake_toolchain(root / "prefix", self.sysroot)

    def run_smoke(self, options=(), **overrides):
        return subprocess.run(
            [
                "bash",
                str(SMOKE),
                "--prefix",
                str(self.prefix),
                "--sysroot",
                str(self.sysroot),
                *options,
                str(SOURCE),
                str(EXPECTED),
            ],
            capture_output=True,
            text=True,
            env=self.rig.env(**overrides),
            timeout=60,
        )

    def assert_transaction_failed(self, proc):
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("guest transaction failed", proc.stderr)

    def assert_guest_never_reached(self, proc):
        self.assertNotEqual(proc.returncode, 0)
        for line in self.rig.calls():
            self.assertNotIn(" put ", line)
            self.assertNotIn(" run ", line)
            self.assertNotIn(" get ", line)


class SmokeFaultInjection(FaultInjectionBase):
    def test_success_runs_the_whole_transaction(self):
        proc = self.run_smoke(options=["--timeout", "7"], FAKE_STDOUT_FILE=str(EXPECTED))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, EXPECTED.read_text())
        calls = self.rig.transport_calls()
        self.assertTrue(any(" put " in line for line in calls))
        self.assertTrue(any(" run " in line for line in calls))
        self.assertEqual(sum(" get " in line for line in calls), 3)
        # Every transport stage is bounded by the requested timeout.
        for line in calls:
            self.assertIn("--timeout 7", line)

    def test_failed_put_fails(self):
        proc = self.run_smoke(FAKE_PUT_FAIL="1")
        self.assert_transaction_failed(proc)
        self.assertIn("put failed", proc.stderr)

    def test_failed_run_transport_fails_even_with_matching_evidence(self):
        proc = self.run_smoke(
            FAKE_RUN_RC="90",
            FAKE_RUN_WRITES_BEFORE_FAIL="1",
            FAKE_STDOUT_FILE=str(EXPECTED),
        )
        self.assert_transaction_failed(proc)
        self.assertIn("run failed at the transport", proc.stderr)

    def test_run_timeout_fails(self):
        proc = self.run_smoke(FAKE_RUN_TIMEOUT="1")
        self.assert_transaction_failed(proc)
        self.assertIn("run failed at the transport", proc.stderr)

    def test_failed_get_stdout_fails(self):
        proc = self.run_smoke(FAKE_STDOUT_FILE=str(EXPECTED), FAKE_GET_FAIL="stdout")
        self.assert_transaction_failed(proc)
        self.assertIn("get failed", proc.stderr)

    def test_failed_get_stderr_fails(self):
        proc = self.run_smoke(FAKE_STDOUT_FILE=str(EXPECTED), FAKE_GET_FAIL="stderr")
        self.assert_transaction_failed(proc)
        self.assertIn("get failed", proc.stderr)

    def test_failed_get_status_fails(self):
        proc = self.run_smoke(FAKE_STDOUT_FILE=str(EXPECTED), FAKE_GET_FAIL="status")
        self.assert_transaction_failed(proc)

    def test_missing_status_fails(self):
        proc = self.run_smoke(FAKE_STDOUT_FILE=str(EXPECTED), FAKE_STATUS_OMIT="1")
        self.assert_transaction_failed(proc)

    def test_non_numeric_status_fails(self):
        proc = self.run_smoke(
            FAKE_STDOUT_FILE=str(EXPECTED), FAKE_GUEST_STATUS_TEXT="banana"
        )
        self.assert_transaction_failed(proc)
        self.assertIn("non-negative integer", proc.stderr)

    def test_empty_status_fails(self):
        proc = self.run_smoke(
            FAKE_STDOUT_FILE=str(EXPECTED), FAKE_STATUS_EMPTY="1"
        )
        self.assert_transaction_failed(proc)
        self.assertIn("non-negative integer", proc.stderr)

    def test_nonzero_guest_exit_fails(self):
        proc = self.run_smoke(
            FAKE_STDOUT_FILE=str(EXPECTED), FAKE_GUEST_STATUS="7"
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("guest program exited 7", proc.stderr)

    def test_output_mismatch_fails(self):
        proc = self.run_smoke(FAKE_STDOUT_TEXT="wrong output\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not match", proc.stderr)

    def test_partial_output_fails(self):
        proc = self.run_smoke(
            FAKE_STDOUT_FILE=str(EXPECTED),
            FAKE_GET_FAIL="stdout",
            FAKE_GET_PARTIAL="1",
        )
        self.assert_transaction_failed(proc)

    def test_stale_matching_output_cannot_pass_a_failed_get(self):
        # The fake writes complete, matching stdout and a zero status, then
        # fails the stderr get: the transaction must still fail.
        proc = self.run_smoke(
            FAKE_STDOUT_FILE=str(EXPECTED),
            FAKE_GET_FAIL="stderr",
            FAKE_GET_WRITE_THEN_FAIL="1",
        )
        self.assert_transaction_failed(proc)

    def test_readelf_failure_is_fatal_before_the_guest(self):
        proc = self.run_smoke(FAKE_READELF_FAIL="1")
        self.assert_guest_never_reached(proc)
        self.assertIn("readelf failed", proc.stderr)

    def test_non_dynamic_binary_is_rejected_before_the_guest(self):
        proc = self.run_smoke(FAKE_READELF_NO_INTERP="1")
        self.assert_guest_never_reached(proc)
        self.assertIn("PT_INTERP", proc.stderr)

    def test_rig_unavailable_has_a_clear_message(self):
        proc = self.run_smoke(FAKE_PING_RC="1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("rig is not running", proc.stderr)


class GuestTransactionContract(unittest.TestCase):
    """Direct tests of scripts/lib/guest-txn.sh's fail-closed exit statuses."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="guest-txn-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rig = fake_rig.FakeRig(self.root / "rig")
        self.binary = self.root / "prog"
        self.binary.write_text("fake binary\n")
        self.runner = self.root / "prog.run"
        self.runner.write_text("#!/bin/sh\n")
        self.host_out = self.root / "host.out"
        self.host_err = self.root / "host.err"
        self.host_status = self.root / "host.status"
        self.host_extra = self.root / "host.extra"

    def run_txn(self, *args, **overrides):
        return subprocess.run(
            ["bash", str(TXN), *args],
            capture_output=True,
            text=True,
            env=self.rig.env(**overrides),
            timeout=60,
        )

    def streams_args(self, guest_bin="/tmp/smoke/prog"):
        return [
            "--timeout",
            "5",
            "--guest-dir",
            "/tmp/smoke",
            "--put",
            str(self.binary),
            guest_bin,
            "--streams",
            guest_bin,
            str(self.host_out),
            str(self.host_err),
            str(self.host_status),
        ]

    def test_streams_success(self):
        proc = self.run_txn(*self.streams_args(), FAKE_STDOUT_TEXT="hi\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.host_out.read_text(), "hi\n")
        self.assertEqual(self.host_err.read_text(), "")
        self.assertEqual(self.host_status.read_text(), "0")
        for line in self.rig.transport_calls():
            self.assertIn("--timeout 5", line)

    def test_generic_success_with_extra_evidence(self):
        guest_bin = "/tmp/hinv/prog"
        proc = self.run_txn(
            "--timeout",
            "5",
            "--guest-dir",
            "/tmp/hinv",
            "--put",
            str(self.binary),
            guest_bin,
            "--put",
            str(self.runner),
            f"{guest_bin}.run",
            "--run",
            f"sh {guest_bin}.run",
            "--get",
            f"{guest_bin}.stdout",
            str(self.host_out),
            "--get",
            f"{guest_bin}.file",
            str(self.host_extra),
            "--status",
            f"{guest_bin}.status",
            str(self.host_status),
            FAKE_RUN_GUEST_BIN=guest_bin,
            FAKE_STDOUT_TEXT="evidence\n",
            FAKE_EXTRA_PATH=f"{guest_bin}.file",
            FAKE_EXTRA_TEXT="fake ELF\n",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.host_out.read_text(), "evidence\n")
        self.assertEqual(self.host_extra.read_text(), "fake ELF\n")
        self.assertEqual(self.host_status.read_text(), "0")

    def test_usage_errors_are_64(self):
        cases = [
            [str(self.binary), "/tmp/x"],  # no --timeout, no mode
            ["--timeout", "5", "--run", "true"],  # no put, no status
            ["--timeout", "5", "--put", str(self.binary), "/tmp/x"],  # no mode
            ["--timeout", "banana", "--streams", "/tmp/x", "a", "b", "c"],
        ]
        for args in cases:
            proc = self.run_txn(*args)
            self.assertEqual(proc.returncode, 64, (args, proc.stderr))

    def test_run_transport_failure_is_90_and_clears_old_evidence(self):
        self.host_out.write_text("old matching output\n")
        self.host_status.write_text("0")
        proc = self.run_txn(
            *self.streams_args(),
            FAKE_RUN_RC="90",
            FAKE_RUN_WRITES_BEFORE_FAIL="1",
            FAKE_STDOUT_TEXT="old matching output\n",
        )
        self.assertEqual(proc.returncode, 90, proc.stderr)
        self.assertFalse(self.host_out.exists(), "old stdout evidence survived")
        self.assertFalse(self.host_status.exists(), "old status evidence survived")

    def test_get_transport_failure_is_91(self):
        proc = self.run_txn(*self.streams_args(), FAKE_GET_FAIL="stdout")
        self.assertEqual(proc.returncode, 91, proc.stderr)

    def test_get_reporting_success_without_a_file_is_92(self):
        # A get that exits zero but writes no target is caught as missing
        # evidence, not accepted as an empty stream.
        proc = self.run_txn(
            *self.streams_args(), FAKE_GET_SILENT_SUCCESS="stdout"
        )
        self.assertEqual(proc.returncode, 92, proc.stderr)

    def test_invalid_status_is_94(self):
        proc = self.run_txn(
            *self.streams_args(), FAKE_GUEST_STATUS_TEXT="banana"
        )
        self.assertEqual(proc.returncode, 94, proc.stderr)
        self.assertFalse(self.host_status.exists())

    def test_failed_put_is_91_and_clears_old_evidence(self):
        self.host_out.write_text("old matching output\n")
        proc = self.run_txn(*self.streams_args(), FAKE_PUT_FAIL="1")
        self.assertEqual(proc.returncode, 91, proc.stderr)
        self.assertFalse(self.host_out.exists(), "old stdout evidence survived")

    def test_rig_unavailable_is_93_with_a_clear_message(self):
        proc = self.run_txn(*self.streams_args(), FAKE_PING_RC="1")
        self.assertEqual(proc.returncode, 93, proc.stderr)
        self.assertIn("rig is not running", proc.stderr)

    def test_lock_release_lets_the_next_transaction_run(self):
        first = self.run_txn(*self.streams_args(), FAKE_STDOUT_TEXT="one\n")
        second = self.run_txn(*self.streams_args(), FAKE_STDOUT_TEXT="two\n")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.host_out.read_text(), "two\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
