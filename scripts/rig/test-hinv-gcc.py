#!/usr/bin/env python3
"""Host-only unit tests for scripts/rig/hinv-gcc.sh's flag translation.

--print-commands is pure: it needs no toolchain, tree or rig, so the
MIPSpro-to-GCC mapping recorded in docs/hinv-gcc.md can be checked here.

Run: python3 scripts/rig/test-hinv-gcc.py
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "rig" / "hinv-gcc.sh"

TREE = "/tmp/fake-irix-tree"
OUT = "/tmp/fake-hinv-out"
SYSROOT = "/tmp/fake-sysroot"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


class FlagTranslationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run(
            "--print-commands",
            "--tree",
            TREE,
            "--out",
            OUT,
            "--sysroot",
            SYSROOT,
        )
        if cls.result.returncode != 0:
            raise AssertionError(cls.result.stderr)
        lines = cls.result.stdout.splitlines()
        cls.compile_line = next(line for line in lines if line.startswith("compile:"))
        cls.link_line = next(line for line in lines if line.startswith("link:"))

    def test_compile_finds_the_tree_headers_ahead_of_the_sysroot(self):
        self.assertIn(f"-I{TREE}/irix/kern", self.compile_line)
        self.assertIn(f"-I{TREE}/irix/usr/include", self.compile_line)

    def test_compile_keeps_the_tree_abi(self):
        self.assertIn("--sysroot=" + SYSROOT, self.compile_line)
        self.assertIn("-mips3", self.compile_line)
        self.assertIn("-mabi=n32", self.compile_line)
        self.assertIn("-O", self.compile_line.split())

    def test_compile_uses_the_c89_mode_the_tree_code_needs(self):
        self.assertIn("-std=gnu89", self.compile_line)

    def test_compile_records_dependencies(self):
        self.assertIn("-MD", self.compile_line)

    def test_compile_drops_the_mipspro_only_flags(self):
        for flag in ("-nostdinc", "-nostdlib", "-quickstart_info", "-woff", "-MDupdate"):
            self.assertNotIn(flag, self.compile_line)
        self.assertNotIn("-I/usr/include", self.compile_line)

    def test_link_translates_the_rld_marker_literally(self):
        self.assertIn("-Wl,-I,/lib32/libc.so.1", self.link_line)
        self.assertIn("-Wl,-rpath,/lib32", self.link_line)

    def test_link_lets_the_specs_supply_startfiles_and_library_paths(self):
        self.assertNotIn("-nostdlib", self.link_line)
        self.assertNotIn("-L//usr/lib32", self.link_line)
        self.assertNotIn("-quickstart_info", self.link_line)

    def test_commands_use_the_expected_inputs_and_outputs(self):
        self.assertIn(f"{TREE}/irix/cmd/hinv/hinv.c", self.compile_line)
        self.assertIn(f"-o {OUT}/hinv.o", self.compile_line)
        self.assertIn(f"{OUT}/hinv.o", self.link_line)
        self.assertIn(f"-o {OUT}/hinv", self.link_line)


class PreconditionsTest(unittest.TestCase):
    def test_print_commands_requires_a_sysroot(self):
        result = run("--print-commands", "--tree", TREE, "--out", OUT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--sysroot", result.stderr)

    def test_unknown_option_is_rejected(self):
        result = run("--frobnicate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option", result.stderr)


if __name__ == "__main__":
    unittest.main()
