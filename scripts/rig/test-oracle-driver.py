#!/usr/bin/env python3
"""Unit tests for the pure logic in oracle-driver.py (issue #3).

The install and capture themselves are integration tests against the live
emulator; these pin the pieces that are cheap to get subtly wrong: classifying
inst's `go` transcript, the media/product sets the install walks, and the
sysroot manifest's coverage of headers, startfiles and libc/libm per ABI.
"""

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    "oracle_driver", HERE / "oracle-driver.py"
)
drv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drv)

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
    def test_success_wins_over_earlier_error_lookalike(self):
        self.assertEqual(drv.classify_go(SUCCESS_TAIL), "success")

    def test_unresolved_conflict(self):
        self.assertEqual(drv.classify_go(CONFLICT_TAIL), "conflict")

    def test_error(self):
        self.assertEqual(drv.classify_go(ERROR_TAIL), "error")

    def test_failure_line(self):
        self.assertEqual(drv.classify_go("Installations and removals failed."), "error")

    def test_quiet_transcript_is_unclassified(self):
        self.assertIsNone(drv.classify_go("Installing/removing files .. 47%"))


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
