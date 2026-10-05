#!/usr/bin/env python3
"""Host-only unit tests for scripts/runtime/rebuild-libc.py (issue #9).

The driver's compile/link/guest phases are the integration test against the
real tree, cross and guest; these tests pin the pure translations and the
preconditions that must fail before any of that runs. The tree is never read
here.

The module name has a dash, so it is loaded by path.

Run: python3 scripts/runtime/test-rebuild-libc.py
"""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "rebuild-libc.py"

spec = importlib.util.spec_from_file_location("rebuild_libc", SCRIPT)
rebuild = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild)


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
        prefix = (HERE.parent.parent / ".scratch/toolchain-16.2/prefix").resolve()
        if not (prefix / "bin" / f"{rebuild.TARGET}-gcc").exists():
            self.skipTest("cross not built in this checkout")
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_driver("--tree", tmp, "--prefix", str(prefix))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an IRIX tree", result.stderr)


if __name__ == "__main__":
    unittest.main()
