#!/usr/bin/env python3
"""Unit tests for the pure logic in oracle-driver.py (issue #3).

The install and capture themselves are integration tests against the live
emulator; these pin the pieces that are cheap to get subtly wrong: classifying
inst's `go` transcript, the media/product sets the install walks, and the
sysroot manifest's coverage of headers, startfiles and libc/libm per ABI.
"""

import importlib.util
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    "oracle_driver", HERE / "oracle-driver.py"
)
drv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drv)

install_driver = drv.install_driver

SOCKET = "/rig/iris.sock"
IC = "/rig/iris/target/release/iris-ci"

SUCCESS_TAIL = """
Checking dependencies ..  99% 100% Done.
Installations and removals were successful.
You may continue with installations or quit now.
"""

CONFLICT_TAIL = """
Conflicts must be resolved before continuing.
Resolve conflicts by typing "conflicts choice choice ..."
"""

ERROR_TAIL = """
exitop('...'): ERROR: cannot create /usr/lib/thing
"""


class ClassifyGo(unittest.TestCase):
    def test_success_tail_is_success(self):
        self.assertEqual(drv.classify_go(SUCCESS_TAIL), "success")

    def test_unresolved_conflict(self):
        self.assertEqual(drv.classify_go(CONFLICT_TAIL), "conflict")

    def test_error(self):
        self.assertEqual(drv.classify_go(ERROR_TAIL), "error")

    def test_failure_line(self):
        self.assertEqual(drv.classify_go("Installations and removals failed."), "error")

    def test_quiet_transcript_is_unclassified(self):
        self.assertIsNone(drv.classify_go("Installing/removing files .. 47%"))

    def test_an_earlier_error_beats_a_later_success_line(self):
        self.assertEqual(drv.classify_go(ERROR_TAIL + SUCCESS_TAIL), "error")

    def test_an_earlier_conflict_beats_a_later_success_line(self):
        self.assertEqual(drv.classify_go(CONFLICT_TAIL + SUCCESS_TAIL), "conflict")

    def test_the_shared_classifier_is_used(self):
        self.assertIs(
            drv.classify_go, install_driver.classify_install_transcript
        )


class ProductVerification(unittest.TestCase):
    VERSIONS = """
compiler_dev   7.3
c_dev          7.3
c_fe           7.3
irix_dev       6.5.7m
dev            6.5.7m
"""

    def test_no_missing_products_when_all_are_named(self):
        self.assertEqual(
            drv.missing_products("compiler_dev c_dev c_fe", self.VERSIONS), []
        )

    def test_missing_product_is_reported(self):
        self.assertEqual(
            drv.missing_products("compiler_dev compiler_eoe", self.VERSIONS),
            ["compiler_eoe"],
        )

    def test_a_child_product_does_not_satisfy_the_parent(self):
        self.assertEqual(
            drv.missing_products("compiler_eoe", "compiler_eoe.sw64.lib 7.4\n"),
            ["compiler_eoe"],
        )

    def test_partial_token_does_not_satisfy(self):
        self.assertEqual(drv.missing_products("c_dev", "xc_dev 7.3\n"), ["c_dev"])

    def test_product_check_accepts_and_rejects_on_stdin(self):
        with mock.patch.object(sys, "stdin", io.StringIO(self.VERSIONS)):
            self.assertEqual(drv.product_check("c_dev c_fe"), 0)
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(self.VERSIONS)), \
             redirect_stderr(stderr):
            self.assertEqual(drv.product_check("c_dev compiler_eoe"), 1)
        self.assertIn("compiler_eoe", stderr.getvalue())


class OracleSets(unittest.TestCase):
    def test_three_sets_in_install_order(self):
        self.assertEqual([s[0] for s in drv.ORACLE_SETS], ["libs", "mipspro", "cee"])

    def test_headers_and_startfiles_come_from_development_libraries(self):
        slug, media, products = drv.ORACLE_SETS[0]
        self.assertIn("Development Libraries", media)
        self.assertIn("irix_dev.sw.headers", products)
        self.assertIn("dev.sw.lib", products)

    def test_compiler_is_mipspro_7_3_from_the_all_compiler_cd(self):
        slug, media, products = drv.ORACLE_SETS[1]
        self.assertIn("All-Compiler", media)
        for product in ("compiler_dev", "c_dev", "c_fe"):
            self.assertIn(product, products)

    def test_runtime_is_cee_7_4(self):
        slug, media, products = drv.ORACLE_SETS[2]
        self.assertEqual(media, "Compiler Execution Environment 7.4.iso")
        self.assertIn("compiler_eoe", products)


class EnsureShellSocket(unittest.TestCase):
    """Every ensure_shell path must pin the selected socket on iris-ci calls."""

    def setUp(self):
        self.rig = drv.Rig(SOCKET, log_path=None, echo=False)
        self.calls = []

        def fake_run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0)

        self.run_patcher = mock.patch.object(
            install_driver.subprocess, "run", side_effect=fake_run
        )
        self.run_patcher.start()
        self.addCleanup(self.run_patcher.stop)

        env_patcher = mock.patch.dict(
            os.environ, {"IRIS_SOCKET": "/tmp/iris.sock"}
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def drive(self, pattern):
        with mock.patch.object(drv.Rig, "rpc"), \
             mock.patch.object(drv.Rig, "send"), \
             mock.patch.object(drv.Rig, "expect", return_value=("", pattern)):
            drv.ensure_shell(self.rig, IC)

    def assert_pinned(self):
        self.assertTrue(self.calls, "expected at least one iris-ci call")
        for argv, kwargs in self.calls:
            self.assertEqual(argv[0], IC)
            self.assertEqual(argv[1:3], ["--socket", SOCKET])
            self.assertEqual(kwargs["env"]["IRIS_SOCKET"], SOCKET)

    def test_already_at_shell_runs_no_command(self):
        self.drive("# ")
        self.assertEqual(self.calls, [])

    def test_login_prompt_pins_the_socket(self):
        self.drive("login:")
        self.assertEqual([argv[-1] for argv, _ in self.calls], ["login"])
        self.assert_pinned()

    def test_console_login_prompt_pins_the_socket(self):
        self.drive("console login:")
        self.assert_pinned()

    def test_prom_boots_then_logs_in_with_the_socket(self):
        self.drive("Option?")
        self.assertEqual(
            [argv[-1] for argv, _ in self.calls], ["boot", "login"]
        )
        self.assert_pinned()


class SysrootManifest(unittest.TestCase):
    def setUp(self):
        text = (HERE / "sysroot.files").read_text()
        self.entries = [line.strip() for line in text.splitlines() if line.strip()]

    def test_all_entries_are_relative(self):
        for entry in self.entries:
            with self.subTest(entry=entry):
                self.assertFalse(entry.startswith("/"))
                self.assertNotIn("..", entry.split("/"))

    def test_captures_the_full_header_tree(self):
        self.assertIn("usr/include", self.entries)

    def test_captures_startfiles_for_every_abi(self):
        for entry in ("usr/lib/crt1.o", "usr/lib32/crt1.o", "usr/lib64/crt1.o",
                      "usr/lib/crtn.o", "usr/lib32/crtn.o", "usr/lib64/crtn.o"):
            with self.subTest(entry=entry):
                self.assertIn(entry, self.entries)

    def test_captures_libc_and_libm_for_o32_and_n32(self):
        for entry in ("usr/lib/libc.so", "usr/lib/libm.so",
                      "usr/lib32/libc.so", "usr/lib32/libm.so",
                      "lib/libc.so.1", "lib32/libc.so.1"):
            with self.subTest(entry=entry):
                self.assertIn(entry, self.entries)

    def test_captures_the_n64_abi_libraries(self):
        for entry in ("usr/lib64/abi/crt1.o", "usr/lib64/abi/libc.so",
                      "usr/lib64/abi/libm.a"):
            with self.subTest(entry=entry):
                self.assertIn(entry, self.entries)


if __name__ == "__main__":
    unittest.main(verbosity=2)
