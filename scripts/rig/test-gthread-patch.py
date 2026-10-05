#!/usr/bin/env python3
"""Host-only regression for the IRIX gthread capability selection (issue #31).

The recursive-mutex defect came from carried gthr-posix.h hunks that
commented out pthread capability the captured 6.5.7m headers actually
declare. This test keeps the selection honest without a guest:

  * the patch header no longer carries the "IRIX lacks" rationale, and the
    patch disables no gthr-posix.h reference;
  * applying the patch's gthr-posix.h section (if any) to the pristine GCC
    16.2.0 header yields the upstream recursive selection: the
    pthread_mutexattr_settype call with PTHREAD_MUTEX_RECURSIVE, the
    pthread_equal and sched_yield references, and the rwlock typedef and
    entry points;
  * the patched header's selection logic compiles and runs on the host
    (glibc pthreads) and observes recursion, equality, yield and rwlock;
  * the same logic compiles against the captured IRIX headers with the
    cross for o32 and n32, in both the weak-reference and direct-call
    configurations;
  * once the capture ships libpthread.so, its exported symbols are checked
    by name, so a contract the library does not provide fails here instead
    of crashing through a null weak reference.

The runtime leg needs a host C compiler and skips without one; the tarball,
cross, capture and libpthread legs each skip cleanly when their input is
absent. The symbol check fails (does not skip) when libpthread.so is present
but a required entry point is missing. IRIX_PREFIX and IRIX_SYSROOT name the
cross and capture, IRIX_GCC_TARBALL the pinned GCC 16.2.0 tarball; the
defaults are the repository's .scratch tree and the live-capture path.

Run: python3 scripts/rig/test-gthread-patch.py
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PATCH = ROOT / "patches" / "gcc-16.2" / "0004-irix-libgcc-and-runtime.patch"
SYSROOT_FILES = ROOT / "scripts" / "rig" / "sysroot.files"

TARBALL = Path(
    os.environ.get(
        "IRIX_GCC_TARBALL",
        ROOT / ".scratch" / "toolchain-16.2" / "downloads" / "gcc-16.2.0.tar.xz",
    )
)
PREFIX = Path(os.environ.get("IRIX_PREFIX", ROOT / ".scratch" / "toolchain-16.2" / "prefix"))
SYSROOT = Path(
    os.environ.get(
        "IRIX_SYSROOT",
        "/mnt/europa/sgi-toolchain-scratch/rig/oracle/sysroot",
    )
)
TARGET = "mips-sgi-irix6.5"
GTHR_MEMBER = "gcc-16.2.0/libgcc/gthr-posix.h"
CROSS = PREFIX / "bin" / f"{TARGET}-gcc"
NM = PREFIX / "bin" / f"{TARGET}-nm"

# The gthr selection harness. It is plain POSIX C plus the gthr-posix.h
# interface; the two preprocessor defines select the weak-reference or
# direct-call configuration. Under the old commented-out header the second
# recursive try-lock fails (or the file does not compile at all).
HARNESS = textwrap.dedent(
    """\
    #define SUPPORTS_WEAK {supports_weak}
    #define GTHREAD_USE_WEAK {use_weak}
    #include "gthr-posix.h"
    #include <stdio.h>

    int
    main (void)
    {{
      __gthread_recursive_mutex_t mutex;
      __gthread_rwlock_t rwlock;
      int fails = 0;

      if (__gthread_recursive_mutex_init_function (&mutex) != 0)
        return 1;
      if (__gthread_recursive_mutex_trylock (&mutex) != 0)
        fails++;
      if (__gthread_recursive_mutex_trylock (&mutex) != 0)
        fails++;
      if (__gthread_recursive_mutex_unlock (&mutex) != 0)
        fails++;
      if (__gthread_recursive_mutex_unlock (&mutex) != 0)
        fails++;
      if (__gthread_recursive_mutex_destroy (&mutex) != 0)
        fails++;
      if (!__gthread_equal (__gthread_self (), __gthread_self ()))
        fails++;
      if (__gthread_yield () != 0)
        fails++;
      if (pthread_rwlock_init (&rwlock, NULL) != 0)
        fails++;
      if (__gthread_rwlock_trywrlock (&rwlock) != 0)
        fails++;
      if (__gthread_rwlock_unlock (&rwlock) != 0)
        fails++;
      if (pthread_rwlock_destroy (&rwlock) != 0)
        fails++;

      printf ("gthread-selection: %s\\n", fails == 0 ? "PASS" : "FAIL");
      return fails == 0 ? 0 : 1;
    }}
    """
)


def read_patch():
    return PATCH.read_text(encoding="utf-8")


def gthr_section(patch_text):
    """The patch's gthr-posix.h diff section, or an empty string."""
    lines = patch_text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.startswith("diff --git ") and "libgcc/gthr-posix.h" in line:
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("diff --git "):
            end = i
            break
    return "".join(lines[start:end])


def pristine_gthr_bytes():
    """The pristine tarball's gthr-posix.h bytes, read once per run."""
    if not TARBALL.is_file():
        raise unittest.SkipTest(f"pristine tarball not found: {TARBALL}")
    cached = getattr(pristine_gthr_bytes, "cache", None)
    if cached is None:
        with tarfile.open(TARBALL, mode="r:xz") as archive:
            member = archive.getmember(GTHR_MEMBER)
            source = archive.extractfile(member)
            if source is None:
                raise AssertionError(f"cannot extract {GTHR_MEMBER}")
            cached = source.read()
        pristine_gthr_bytes.cache = cached
    return cached


def extract_pristine_gthr(dest):
    """Write the pristine tarball's gthr-posix.h to dest and return it."""
    dest.write_bytes(pristine_gthr_bytes())
    return dest


def patched_gthr(directory):
    """Apply the patch's gthr-posix.h section to the pristine header."""
    root = Path(directory) / "root"
    (root / "libgcc").mkdir(parents=True)
    target = root / "libgcc" / "gthr-posix.h"
    extract_pristine_gthr(target)
    section = gthr_section(read_patch())
    if section:
        proc = subprocess.run(
            ["patch", "-p1", "--batch", "-d", str(root)],
            input=section,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "gthr-posix.h hunks do not apply to the pristine tarball:\n"
                + proc.stdout
                + proc.stderr
            )
    return target


class PatchShape(unittest.TestCase):
    def test_header_drops_the_irix_lacks_rationale(self):
        text = read_patch()
        self.assertNotIn("IRIX 6.5 pthreads lack", text)
        self.assertNotIn("must not reference them", text)

    def test_header_names_the_capture_evidence(self):
        text = read_patch()
        self.assertIn("libpthread.so", text)
        self.assertIn("sysroot.files", text)
        self.assertIn("PTHREAD_MUTEX_RECURSIVE", text)

    def test_patch_disables_no_gthr_reference(self):
        text = read_patch()
        for disabled in (
            "/*__gthrw(",
            "/*typedef pthread_rwlock_t",
            "//      if (!__r)",
            "//\t__r = __gthrw_(pthread_mutexattr_settype)",
        ):
            self.assertNotIn(disabled, text)

    def test_sysroot_files_carry_both_pthread_libraries(self):
        files = SYSROOT_FILES.read_text(encoding="utf-8").split()
        self.assertIn("usr/lib/libpthread.so", files)
        self.assertIn("usr/lib32/libpthread.so", files)


class PatchedHeader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_result_keeps_recursive_mutex_selection(self):
        header = patched_gthr(self.tmp.name).read_text(encoding="utf-8")
        self.assertIn("__gthrw(pthread_mutexattr_settype)", header)
        self.assertIn(
            "__r = __gthrw_(pthread_mutexattr_settype) (&__attr,", header
        )
        self.assertIn("PTHREAD_MUTEX_RECURSIVE);", header)

    def test_result_keeps_equal_yield_and_rwlock(self):
        header = patched_gthr(self.tmp.name).read_text(encoding="utf-8")
        for restored in (
            "__gthrw(pthread_equal)",
            "__gthrw(sched_yield)",
            "typedef pthread_rwlock_t __gthread_rwlock_t;",
            "__gthrw(pthread_rwlock_tryrdlock)",
            "__gthread_rwlock_tryrdlock",
        ):
            self.assertIn(restored, header)
        for disabled in (
            "/*__gthrw(",
            "/*typedef pthread_rwlock_t",
            "//      if (!__r)",
        ):
            self.assertNotIn(disabled, header)

    @unittest.skipUnless(
        shutil.which("cc") or shutil.which("gcc") or shutil.which("clang"),
        "no host C compiler available",
    )
    def test_patched_selection_runs_on_posix_host(self):
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        header = patched_gthr(self.tmp.name)
        source = Path(self.tmp.name) / "harness.c"
        source.write_text(HARNESS.format(supports_weak=1, use_weak=1))
        binary = Path(self.tmp.name) / "harness"
        build = subprocess.run(
            [cc, "-pthread", "-I", str(header.parent), "-o", str(binary), str(source)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(build.returncode, 0, build.stderr)
        run = subprocess.run(
            [str(binary)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("gthread-selection: PASS", run.stdout)


class CaptureCompile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def require_cross(self):
        if not CROSS.is_file():
            self.skipTest(f"cross compiler not found: {CROSS}")
        if not SYSROOT.is_dir():
            self.skipTest(f"capture sysroot not found: {SYSROOT}")

    def test_patched_header_compiles_against_capture(self):
        self.require_cross()
        header = patched_gthr(self.tmp.name)
        for weak in (1, 0):
            source = Path(self.tmp.name) / f"harness-weak{weak}.c"
            source.write_text(HARNESS.format(supports_weak=weak, use_weak=weak))
            for abi in ("32", "n32"):
                obj = Path(self.tmp.name) / f"harness-weak{weak}-{abi}.o"
                proc = subprocess.run(
                    [
                        str(CROSS),
                        f"--sysroot={SYSROOT}",
                        f"-mabi={abi}",
                        "-I",
                        str(header.parent),
                        "-c",
                        str(source),
                        "-o",
                        str(obj),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"mabi={abi} weak={weak}:\n{proc.stderr}",
                )

    def test_capture_libpthread_exports_the_contract(self):
        self.require_cross()
        required = (
            "pthread_equal",
            "pthread_mutexattr_settype",
            "pthread_rwlock_init",
            "pthread_rwlock_destroy",
            "pthread_rwlock_rdlock",
            "pthread_rwlock_tryrdlock",
            "pthread_rwlock_wrlock",
            "pthread_rwlock_trywrlock",
            "pthread_rwlock_unlock",
        )
        libraries = [
            SYSROOT / "usr/lib/libpthread.so",
            SYSROOT / "usr/lib32/libpthread.so",
        ]
        present = [library for library in libraries if library.exists()]
        if not present:
            self.skipTest(
                "capture has no libpthread.so yet; recapture after "
                "scripts/rig/sysroot.files and rerun"
            )
        self.assertEqual(
            len(present),
            len(libraries),
            "the capture ships only one of the two libpthread.so link libraries",
        )
        for library in present:
            proc = subprocess.run(
                [str(NM), "-D", "--defined-only", str(library)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            defined = set()
            for line in proc.stdout.splitlines():
                parts = line.split()
                if parts:
                    defined.add(parts[-1].split("@")[0])
            missing = sorted(symbol for symbol in required if symbol not in defined)
            self.assertEqual(missing, [], f"{library} is missing: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
