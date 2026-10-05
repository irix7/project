#!/usr/bin/env python3
"""Host-only unit tests for scripts/runtime/rebuild-libc.py (issue #9).

The driver's compile/link/guest phases are the integration test against the
real tree, cross and guest; these tests pin the pure translations and the
preconditions that must fail before any of that runs. The tree is never read
here.

The guest phase and the ELF proof are exercised against the same fake rig
that scripts/smoke/test-smoke.py uses (scripts/rig/fake_rig.py), so every
false-pass path audit C10 names -- failed transport with matching evidence,
stale result files, missing or invalid status, a failed readelf inspection
that prints nothing -- fails without a guest.

The module name has a dash, so it is loaded by path.

Run: python3 scripts/runtime/test-rebuild-libc.py
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "rebuild-libc.py"

spec = importlib.util.spec_from_file_location("rebuild_libc", SCRIPT)
rebuild = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fake_rig = _load("fake_rig", HERE.parent / "rig" / "fake_rig.py")


class MapTreePathTest(unittest.TestCase):
    def test_tree_supplement_comes_first_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            (tree / "irix/usr/include/rpcsvc").mkdir(parents=True)
            (tree / "irix/usr/include/rpcsvc/yp_prot.h").write_text("")
            sysroot = Path(tmp) / "sysroot"
            self.assertEqual(
                rebuild.map_tree_path(tree, sysroot, "/usr/include/rpcsvc"),
                str(tree / "irix/usr/include/rpcsvc"),
            )

    def test_sysroot_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            sysroot = Path(tmp) / "sysroot"
            self.assertEqual(
                rebuild.map_tree_path(tree, sysroot, "/usr/include/sys"),
                str(sysroot / "usr/include/sys"),
            )

    def test_other_paths_pass_through(self):
        tree, sysroot = Path("/tree"), Path("/sysroot")
        self.assertEqual(rebuild.map_tree_path(tree, sysroot, "../inc"), "../inc")


class LocalFlagsTest(unittest.TestCase):
    def test_defines_and_includes_are_kept_and_mapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            sysroot = Path(tmp) / "sysroot"
            flags = rebuild.local_flags(
                tree,
                sysroot,
                {
                    "LCDEFS": "-D_OLD_TERMIOS",
                    "LCINCS": "-I/usr/include/rpcsvc",
                    "LCOPTS": "-OPT:Olimit=0",
                },
                ("LCDEFS", "LCINCS", "LCOPTS"),
            )
            self.assertIn("-D_OLD_TERMIOS", flags)
            self.assertEqual(flags[-1], "-I" + str(sysroot / "usr/include/rpcsvc"))

    def test_mipspro_options_are_dropped(self):
        flags = rebuild.local_flags(
            Path("/tree"), Path("/sysroot"), {"LCOPTS": "-fullwarn"}, ("LCOPTS",)
        )
        self.assertEqual(flags, [])


class ManifestVarsTest(unittest.TestCase):
    def test_rows_are_grouped_by_subdir(self):
        rows = [
            ("var", "term", "LCDEFS", "-D_OLD_TERMIOS"),
            ("var", "math", "SUBDIR_ASINCS", "-I../../inc"),
            ("c", "math", "sqrt.s", ""),
        ]
        variables = rebuild.manifest_vars(rows)
        self.assertEqual(variables["term"]["LCDEFS"], "-D_OLD_TERMIOS")
        self.assertEqual(variables["math"]["SUBDIR_ASINCS"], "-I../../inc")
        self.assertNotIn("sqrt.s", variables)


class CFlagsTest(unittest.TestCase):
    def test_include_order_is_gcc_then_tree_then_sysroot(self):
        flags = rebuild.c_flags(Path("/tree"), Path("/sysroot"), "/gcc/inc")
        gcc_index = flags.index("-isystem/gcc/inc")
        tree_index = flags.index("-isystem/tree/irix/usr/include")
        sysroot_index = flags.index("-isystem/sysroot/usr/include")
        self.assertLess(gcc_index, tree_index)
        self.assertLess(tree_index, sysroot_index)

    def test_no_default_include_dirs(self):
        flags = rebuild.c_flags(Path("/tree"), Path("/sysroot"), "/gcc/inc")
        self.assertIn("-nostdinc", flags)

    def test_the_o32_small_data_boundary_and_defines(self):
        flags = rebuild.c_flags(Path("/tree"), Path("/sysroot"), "/gcc/inc")
        self.assertIn("-G", flags)
        self.assertEqual(flags[flags.index("-G") + 1], "0")
        self.assertIn("-D_LIBC_NONSHARED", flags)
        self.assertIn("-D_SGI_MP_SOURCE", flags)


class O32BoundaryTest(unittest.TestCase):
    def test_quad_sources_named_by_the_leaf_are_excluded(self):
        variables = {
            "math": {
                "QUAD_WORD_CFILES": "atoq.c atold.c",
                "QUAD_WORD_ASFILES": "qfinite.s qwmultu.s",
            }
        }
        excludes = rebuild.o32_quad_excludes(variables)
        self.assertEqual(
            set(excludes),
            {
                "math/atoq.c",
                "math/atold.c",
                "math/qfinite.s",
                "math/qwmultu.s",
            },
        )
        for reason in excludes.values():
            self.assertIn("o32", reason.lower())

    def test_no_quad_variables_means_no_exclusions(self):
        self.assertEqual(rebuild.o32_quad_excludes({}), {})

    def test_bcmp_is_assembled_with_its_define(self):
        self.assertEqual(
            rebuild.PER_FILE_DEFINES["strings/bcmp.s"], ["-DISBCMP"]
        )


class ErrataTest(unittest.TestCase):
    def test_errata_are_scoped_to_specific_files(self):
        self.assertIn("sys/mq_open.c", rebuild.ERRATA)
        self.assertIn("locale/sgi_ffmtmsg.c", rebuild.ERRATA)


class PreconditionsTest(unittest.TestCase):
    def run_driver(self, *args):
        return subprocess.run(
            ["python3", str(SCRIPT), *args], capture_output=True, text=True
        )

    def test_help(self):
        result = self.run_driver("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--tree", result.stdout)

    def test_tree_is_required(self):
        result = self.run_driver()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--tree", result.stderr)

    def test_missing_cross_fails_before_touching_the_tree(self):
        result = self.run_driver(
            "--tree", "/nonexistent-tree", "--prefix", "/nonexistent-prefix"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cross tool not found", result.stderr)

    def test_fake_tree_is_rejected_with_a_usable_cross(self):
        prefix = (HERE.parent.parent / ".scratch/toolchain-16.2.0/prefix").resolve()
        if not (prefix / "bin" / f"{rebuild.TARGET}-gcc").exists():
            self.skipTest("cross not built in this checkout")
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_driver("--tree", tmp, "--prefix", str(prefix))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an IRIX tree", result.stderr)


class GuestRetrievalFailClosed(unittest.TestCase):
    """The runtime guest phase rejects every failed transport (audit C10)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="runtime-txn-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rig = fake_rig.FakeRig(self.root / "rig")
        self.out = self.root / "out"
        self.out.mkdir()
        self.binary = self.root / "hello-static"
        self.binary.write_text("fake binary\n")

    def call(self, **overrides):
        with mock.patch.dict(os.environ, self.rig.env(**overrides), clear=True):
            return rebuild.run_on_guest(self.binary, self.out, 5)

    def test_success_returns_the_guest_status_and_streams(self):
        status, host_out, host_err = self.call(FAKE_STDOUT_TEXT="hello\n")
        self.assertEqual(status, "0")
        self.assertEqual(host_out.read_text(), "hello\n")
        self.assertTrue(host_err.is_file())
        self.assertTrue((self.out / "hello.status").is_file())

    def test_matching_evidence_cannot_pass_a_failed_get(self):
        with self.assertRaises(rebuild.BuildError) as ctx:
            self.call(
                FAKE_STDOUT_TEXT="hello\n",
                FAKE_GET_FAIL="stderr",
                FAKE_GET_WRITE_THEN_FAIL="1",
            )
        self.assertIn("guest transaction failed", str(ctx.exception))

    def test_failed_run_is_rejected_even_with_written_output(self):
        with self.assertRaises(rebuild.BuildError):
            self.call(
                FAKE_RUN_RC="90",
                FAKE_RUN_WRITES_BEFORE_FAIL="1",
                FAKE_STDOUT_TEXT="hello\n",
            )

    def test_old_evidence_cannot_survive_a_failed_get(self):
        (self.out / "hello.stdout").write_text("OLD\n")
        (self.out / "hello.status").write_text("0")
        with self.assertRaises(rebuild.BuildError):
            self.call(FAKE_STDOUT_TEXT="hello\n", FAKE_GET_FAIL="stdout")
        self.assertFalse((self.out / "hello.stdout").exists())
        self.assertFalse((self.out / "hello.status").exists())

    def test_missing_status_is_rejected(self):
        with self.assertRaises(rebuild.BuildError):
            self.call(FAKE_STATUS_OMIT="1")

    def test_non_numeric_status_is_rejected(self):
        with self.assertRaises(rebuild.BuildError) as ctx:
            self.call(FAKE_GUEST_STATUS_TEXT="banana")
        self.assertIn("non-negative integer", str(ctx.exception))


class StaticElfProof(unittest.TestCase):
    """readelf inspection failures cannot count as absence proof (audit C10)."""

    def fake_readelf(self, output="", rc=0):
        tmp = tempfile.TemporaryDirectory(prefix="readelf-")
        self.addCleanup(tmp.cleanup)
        script = Path(tmp.name) / "readelf"
        script.write_text(f"#!/bin/sh\ncat <<'READELF-EOF'\n{output}READELF-EOF\nexit {rc}\n")
        script.chmod(0o755)
        return str(script)

    def test_readelf_failure_is_not_proof(self):
        with self.assertRaises(rebuild.BuildError) as ctx:
            rebuild.static_elf_proof(self.fake_readelf("", rc=1), Path("/nonexistent"))
        self.assertIn("readelf failed", str(ctx.exception))

    def test_empty_success_output_is_not_proof(self):
        with self.assertRaises(rebuild.BuildError) as ctx:
            rebuild.static_elf_proof(self.fake_readelf("", rc=0), Path("/nonexistent"))
        self.assertIn("no output", str(ctx.exception))

    def test_interp_fails_the_proof(self):
        with self.assertRaises(rebuild.BuildError) as ctx:
            rebuild.static_elf_proof(
                self.fake_readelf("  INTERP 0x1\n", rc=0), Path("/nonexistent")
            )
        self.assertIn("PT_INTERP", str(ctx.exception))

    def test_needed_fails_the_proof(self):
        with self.assertRaises(rebuild.BuildError) as ctx:
            rebuild.static_elf_proof(
                self.fake_readelf("  NEEDED libc.so.1\n", rc=0), Path("/nonexistent")
            )
        self.assertIn("NEEDED", str(ctx.exception))

    def test_clean_output_is_proof(self):
        proof = rebuild.static_elf_proof(
            self.fake_readelf("ELF header\n", rc=0), Path("/nonexistent")
        )
        self.assertEqual(proof, "ELF header\n")


if __name__ == "__main__":
    unittest.main()
