#!/usr/bin/env python3
"""Unit tests for the guest-free logic in scripts/smoke.sh (issue #5).

The compile/ship/run/diff path itself is an integration test against the live
guest — the harness's reason to exist. These tests pin the parts that can run
on any host: argument validation and the precondition messages that must
appear before the rig is consulted. Nothing here talks to a guest.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE = ROOT / "scripts" / "smoke.sh"
SOURCE = ROOT / "oracle" / "hello.c"
EXPECTED = ROOT / "scripts" / "smoke" / "hello.expected"


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
