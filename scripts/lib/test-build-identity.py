#!/usr/bin/env python3
"""Host-only tests for the resumable build identity (issue #26).

The pure helpers in scripts/lib/build-identity.sh are driven through bash,
and scripts/build-toolchain.sh is sourced so its stamp, patch and
reconfiguration decisions run against fake tools, pre-seeded source trees
and temp directories. Nothing is downloaded, extracted or built: the only
patch bytes are synthetic and the only compilers are scripts.

Run: python3 scripts/lib/test-build-identity.py
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIB = REPO_ROOT / "scripts" / "lib" / "build-identity.sh"
BUILD_TOOLCHAIN = REPO_ROOT / "scripts" / "build-toolchain.sh"

PATCH_V1 = textwrap.dedent(
    """\
    --- a/file.txt
    +++ b/file.txt
    @@ -1,3 +1,3 @@
     one
    -two
    +TWO
     three
    """
)

PATCH_V2 = textwrap.dedent(
    """\
    --- a/file.txt
    +++ b/file.txt
    @@ -1,3 +1,3 @@
     one
    -two
    +THREE
     three
    """
)


def run_bash(script: str, *args, env=None, check=True):
    merged = os.environ.copy()
    merged.update(env or {})
    result = subprocess.run(
        ["bash", "-c", script, "bash", *map(str, args)],
        capture_output=True,
        text=True,
        env=merged,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"bash failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="build-identity-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class IdentityHelperTest(TempDirTest):
    APPLY_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        irix_apply_patches "$2" "$3"
        """
    )

    def test_sha256_matches_hashlib(self):
        target = self.tmp / "bytes"
        target.write_bytes(b"irix\n")
        result = run_bash(
            'set -euo pipefail\nsource "$1"\nirix_sha256 "$2"',
            LIB,
            target,
        )
        self.assertEqual(result.stdout.strip(), sha256_file(target))

    def test_identity_digest_matches_hashlib(self):
        result = run_bash(
            'set -euo pipefail\nsource "$1"\nprintf "a\\nb\\n" | irix_identity_digest',
            LIB,
        )
        expected = hashlib.sha256(b"a\nb\n").hexdigest()
        self.assertEqual(result.stdout.strip(), expected)

    def test_default_work_dir_is_version_separated_and_follows_the_work_root(self):
        driver = textwrap.dedent(
            """\
            set -euo pipefail
            source "$1"
            unset IRIX_WORK_ROOT
            irix_default_work_dir /repo 16.2.0
            IRIX_WORK_ROOT=/work irix_default_work_dir /repo 15.3.0
            """
        )
        result = run_bash(driver, LIB)
        lines = result.stdout.split()
        self.assertEqual(lines[0], "/repo/.scratch/toolchain-16.2.0")
        self.assertEqual(lines[1], "/work/toolchain-15.3.0")

    def test_apply_records_the_applied_bytes_and_hash(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "file.txt").write_text("one\ntwo\nthree\n")
        patch = self.tmp / "fix.patch"
        patch.write_text(PATCH_V1)

        run_bash(self.APPLY_DRIVER, LIB, src, patch)
        self.assertEqual((src / "file.txt").read_text(), "one\nTWO\nthree\n")
        marker_dir = src / ".irix-patched.d"
        self.assertEqual((marker_dir / "fix.patch.applied").read_text(), PATCH_V1)
        self.assertEqual(
            (marker_dir / "fix.patch.sha256").read_text().strip(),
            sha256_file(patch),
        )

        again = run_bash(self.APPLY_DRIVER, LIB, src, patch)
        self.assertIn("already applied fix.patch", again.stdout)
        self.assertEqual((src / "file.txt").read_text(), "one\nTWO\nthree\n")

    def test_same_named_patch_with_changed_bytes_is_reversed_and_reapplied(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "file.txt").write_text("one\ntwo\nthree\n")
        patch = self.tmp / "fix.patch"
        patch.write_text(PATCH_V1)
        run_bash(self.APPLY_DRIVER, LIB, src, patch)

        patch.write_text(PATCH_V2)
        changed = run_bash(self.APPLY_DRIVER, LIB, src, patch)
        self.assertIn("fix.patch changed; reversing the applied copy", changed.stdout)
        self.assertEqual((src / "file.txt").read_text(), "one\nTHREE\nthree\n")
        marker_dir = src / ".irix-patched.d"
        self.assertEqual((marker_dir / "fix.patch.applied").read_text(), PATCH_V2)
        self.assertEqual(
            (marker_dir / "fix.patch.sha256").read_text().strip(),
            sha256_file(patch),
        )

    def test_changed_patch_that_cannot_reverse_fails_asking_for_clean(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "file.txt").write_text("one\ntwo\nthree\n")
        patch = self.tmp / "fix.patch"
        patch.write_text(PATCH_V1)
        run_bash(self.APPLY_DRIVER, LIB, src, patch)

        (src / "file.txt").write_text("one\nhand-edited\nthree\n")
        patch.write_text(PATCH_V2)
        failed = run_bash(self.APPLY_DRIVER, LIB, src, patch, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("--clean", failed.stderr)
        self.assertEqual((src / "file.txt").read_text(), "one\nhand-edited\nthree\n")

    def test_changed_patch_that_fails_after_reversal_asks_for_clean(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "file.txt").write_text("one\ntwo\nthree\n")
        patch = self.tmp / "fix.patch"
        patch.write_text(PATCH_V1)
        run_bash(self.APPLY_DRIVER, LIB, src, patch)

        patch.write_text(
            textwrap.dedent(
                """\
                --- a/file.txt
                +++ b/file.txt
                @@ -1,3 +1,3 @@
                 one
                -nine
                +TEN
                 three
                """
            )
        )
        failed = run_bash(self.APPLY_DRIVER, LIB, src, patch, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("stored copy was reversed", failed.stderr)
        self.assertIn("--clean", failed.stderr)

    def test_legacy_marker_with_unknown_bytes_is_refused(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "file.txt").write_text("one\ntwo\nthree\n")
        marker_dir = src / ".irix-patched.d"
        marker_dir.mkdir()
        (marker_dir / "fix.patch").touch()
        patch = self.tmp / "fix.patch"
        patch.write_text(PATCH_V1)

        failed = run_bash(self.APPLY_DRIVER, LIB, src, patch, check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("filename-only marker", failed.stderr)
        self.assertIn("--clean", failed.stderr)

    def test_legacy_marker_is_adopted_when_the_patch_is_already_applied(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "file.txt").write_text("one\nTWO\nthree\n")
        marker_dir = src / ".irix-patched.d"
        marker_dir.mkdir()
        (marker_dir / "fix.patch").touch()
        patch = self.tmp / "fix.patch"
        patch.write_text(PATCH_V1)

        result = run_bash(self.APPLY_DRIVER, LIB, src, patch)
        self.assertIn("adopting the legacy marker", result.stdout)
        self.assertEqual((src / "file.txt").read_text(), "one\nTWO\nthree\n")
        self.assertTrue((marker_dir / "fix.patch.sha256").is_file())
        self.assertFalse((marker_dir / "fix.patch").exists())

    STAMP_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        tool="$2/tool"
        base="$2/stamp"
        if irix_stamp_ok "$base" "$3" "$tool"; then echo hit; else echo miss; fi
        """
    )

    def _record_stamp(self, identity):
        write_exe(
            self.tmp / "tool",
            "#!/usr/bin/env bash\nset -euo pipefail\necho 'fake tool 1.0'\n",
        )
        return run_bash(
            'set -euo pipefail\nsource "$1"\n'
            'irix_write_stamp "$2/stamp" "$3" "$2/tool"',
            LIB,
            self.tmp,
            identity,
        )

    def test_stamp_hits_only_with_matching_identity_and_output(self):
        identity = "component=fake\nversion=1.0\n"
        self._record_stamp(identity)
        hit = run_bash(self.STAMP_DRIVER, LIB, self.tmp, identity)
        self.assertEqual(hit.stdout.strip(), "hit")

        wrong_identity = run_bash(self.STAMP_DRIVER, LIB, self.tmp, "component=fake\nversion=2.0\n")
        self.assertEqual(wrong_identity.stdout.strip(), "miss")

        os.unlink(self.tmp / "stamp.output")
        missing_output = run_bash(self.STAMP_DRIVER, LIB, self.tmp, identity)
        self.assertEqual(missing_output.stdout.strip(), "miss")

    def test_stamp_misses_when_the_installed_tool_changed(self):
        identity = "component=fake\n"
        self._record_stamp(identity)
        write_exe(
            self.tmp / "tool",
            "#!/usr/bin/env bash\nset -euo pipefail\necho 'fake tool 2.0'\n",
        )
        result = run_bash(self.STAMP_DRIVER, LIB, self.tmp, identity)
        self.assertEqual(result.stdout.strip(), "miss")

    def test_stamp_misses_when_the_tool_vanished(self):
        identity = "component=fake\n"
        self._record_stamp(identity)
        os.unlink(self.tmp / "tool")
        result = run_bash(self.STAMP_DRIVER, LIB, self.tmp, identity)
        self.assertEqual(result.stdout.strip(), "miss")

    def test_version_conflicts_name_other_releases_only(self):
        work = self.tmp / "work"
        (work / "stamps").mkdir(parents=True)
        (work / "src" / "gcc-15.2.0").mkdir(parents=True)
        (work / "downloads").mkdir()
        (work / "stamps" / "gcc-15.3.0.installed").touch()
        (work / "stamps" / "gcc-16.2.0.installed.identity").touch()
        (work / "downloads" / "gcc-15.2.0.tar.xz").touch()

        driver = textwrap.dedent(
            """\
            set -euo pipefail
            source "$1"
            irix_gcc_version_conflicts "$2" 16.2.0
            echo ---
            irix_gcc_version_conflicts "$2" 15.2.0
            """
        )
        result = run_bash(driver, LIB, work)
        first, second = result.stdout.split("---")
        self.assertIn("stamps/gcc-15.3.0.installed", first)
        self.assertIn("src/gcc-15.2.0", first)
        self.assertIn("downloads/gcc-15.2.0.tar.xz", first)
        self.assertNotIn("16.2.0", first)
        self.assertIn("stamps/gcc-15.3.0.installed", second)
        self.assertNotIn("15.2.0", second)

    def test_installed_gcc_version_is_read_from_the_prefix(self):
        prefix = self.tmp / "prefix"
        write_exe(
            prefix / "bin" / "mips-sgi-irix6.5-gcc",
            "#!/usr/bin/env bash\nset -euo pipefail\necho 'mips-sgi-irix6.5-gcc (GCC) 15.2.0'\n",
        )
        driver = textwrap.dedent(
            """\
            set -euo pipefail
            source "$1"
            if v=$(irix_installed_gcc_version "$2" mips-sgi-irix6.5); then echo "$v"; else echo none; fi
            if v=$(irix_installed_gcc_version "$2/absent" mips-sgi-irix6.5); then echo "$v"; else echo none; fi
            """
        )
        result = run_bash(driver, LIB, prefix)
        self.assertEqual(
            result.stdout.splitlines(),
            ["mips-sgi-irix6.5-gcc (GCC) 15.2.0", "none"],
        )

    CONFIGURE_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        src="$2/src"
        build="$2/build"
        mkdir -p "$src"
        cat > "$src/configure" <<'EOS'
        #!/usr/bin/env bash
        set -euo pipefail
        echo "configure $*" >> "$CONFIGURE_LOG"
        : > config.status
        EOS
        chmod +x "$src/configure"
        export CONFIGURE_LOG="$2/configure.log"
        export MAKE_LOG="$2/make.log"
        export JOBS=1
        export PATH="$3:$PATH"
        irix_configure_and_make "$src" "$build" "$2/build.log" "$4" --prefix=/x
        irix_configure_and_make "$src" "$build" "$2/build.log" "$4" --prefix=/x
        irix_configure_and_make "$src" "$build" "$2/build.log" "$5" --prefix=/x
        rm -f "$build/.irix-build-identity"
        irix_configure_and_make "$src" "$build" "$2/build.log" "$5" --prefix=/x
        """
    )

    def test_configure_and_make_reconfigures_when_identity_changes(self):
        fakebin = self.tmp / "bin"
        write_exe(
            fakebin / "make",
            "#!/usr/bin/env bash\nset -euo pipefail\necho \"make $*\" >> \"$MAKE_LOG\"\n",
        )
        result = run_bash(
            self.CONFIGURE_DRIVER,
            LIB,
            self.tmp,
            fakebin,
            "identity-one",
            "identity-two",
        )
        self.assertEqual(result.returncode, 0)
        configure_log = (self.tmp / "configure.log").read_text().splitlines()
        self.assertEqual(len(configure_log), 3, configure_log)
        self.assertIn("reconfiguring build", result.stdout)
        make_log = (self.tmp / "make.log").read_text().splitlines()
        self.assertEqual(len(make_log), 8, make_log)
        self.assertEqual(
            (self.tmp / "build" / ".irix-build-identity").read_text(),
            "identity-two",
        )


class BuildToolchainEndToEndTest(TempDirTest):
    """Drive the real script's build functions with fake compilers."""

    GCC_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        GCC_VERSION=16.2.0
        GCC_RECIPE=series
        GCC_SHA256=deadbeef
        GCC_TARBALL=gcc-16.2.0.tar.xz
        LANGUAGES=c
        IRIX_SYSROOT=
        GCC_SERIES_DIR="$2"
        GCC_15X_PATCHES=()
        LOCAL_GCC_PATCHES=()
        TARGET=mips-sgi-irix6.5
        PREFIX="$3"
        STAMPS="$4"
        LOGS="$5"
        BUILD_DIR="$6"
        DOWNLOADS="$7"
        SRC_DIR="$8"
        FAKEBIN="$9"
        JOBS=1
        export PREFIX TARGET
        mkdir -p "$PREFIX/bin" "$STAMPS" "$LOGS" "$BUILD_DIR" "$DOWNLOADS" "$SRC_DIR/gcc-16.2.0"
        cat > "$SRC_DIR/gcc-16.2.0/configure" <<'EOS'
        #!/usr/bin/env bash
        set -euo pipefail
        echo "configure" >> "$CONFIGURE_LOG"
        : > config.status
        EOS
        chmod +x "$SRC_DIR/gcc-16.2.0/configure"
        export PATH="$FAKEBIN:$PATH"
        fetch() { :; }
        build_gcc
        """
    )

    def setUp(self):
        super().setUp()
        self.work = self.tmp / "work"
        self.series = self.tmp / "series"
        self.series.mkdir()
        (self.series / "series").write_text("0001-test.patch\n")
        self.patch = self.series / "0001-test.patch"
        self.patch.write_text(
            textwrap.dedent(
                """\
                --- a/test.txt
                +++ b/test.txt
                @@ -1 +1 @@
                -old
                +new
                """
            )
        )
        self.src = self.work / "src" / "gcc-16.2.0"
        self.src.mkdir(parents=True)
        (self.src / "test.txt").write_text("old\n")

        self.fakebin = self.tmp / "fakebin"
        write_exe(
            self.fakebin / "make",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                echo "make $*" >> "$MAKE_LOG"
                if [ "${1:-}" = install ]; then
                    cat > "$PREFIX/bin/mips-sgi-irix6.5-gcc" <<'EOS'
                #!/usr/bin/env bash
                echo "mips-sgi-irix6.5-gcc (GCC) 16.2.0"
                EOS
                    chmod +x "$PREFIX/bin/mips-sgi-irix6.5-gcc"
                fi
                """
            ),
        )
        self.env = {
            "CONFIGURE_LOG": str(self.tmp / "configure.log"),
            "MAKE_LOG": str(self.tmp / "make.log"),
        }

    IDENTITY_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        GCC_VERSION=16.2.0
        GCC_RECIPE=series
        GCC_SHA256=deadbeef
        GCC_TARBALL=gcc-16.2.0.tar.xz
        LANGUAGES=c
        IRIX_SYSROOT=
        GCC_SERIES_DIR="$2"
        GCC_15X_PATCHES=()
        LOCAL_GCC_PATCHES=()
        digest() { gcc_identity "$@" | irix_identity_digest; }
        base=$(digest --prefix=/p --target=mips-sgi-irix6.5 --enable-languages=c)
        again=$(digest --prefix=/p --target=mips-sgi-irix6.5 --enable-languages=c)
        lang=$(digest --prefix=/p --target=mips-sgi-irix6.5 --enable-languages=c,c++)
        prefix=$(digest --prefix=/q --target=mips-sgi-irix6.5 --enable-languages=c)
        printf '%s\\n%s\\n%s\\n%s\\n' "$base" "$again" "$lang" "$prefix"
        """
    )

    def test_gcc_identity_tracks_options_and_patch_bytes(self):
        result = run_bash(self.IDENTITY_DRIVER, BUILD_TOOLCHAIN, self.series)
        base, again, lang, prefix = result.stdout.split()
        self.assertEqual(base, again)
        self.assertEqual(len({base, lang, prefix}), 3)

        self.patch.write_text(self.patch.read_text() + "\n")
        changed = run_bash(self.IDENTITY_DRIVER, BUILD_TOOLCHAIN, self.series)
        self.assertNotEqual(changed.stdout.split()[0], base)

    def _build(self):
        return run_bash(
            self.GCC_DRIVER,
            BUILD_TOOLCHAIN,
            self.series,
            self.work / "prefix",
            self.work / "stamps",
            self.work / "logs",
            self.work / "build",
            self.work / "downloads",
            self.work / "src",
            self.fakebin,
            env=self.env,
        )

    def _configure_count(self):
        log = self.tmp / "configure.log"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def _make_count(self):
        log = self.tmp / "make.log"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def test_fake_gcc_build_hits_then_rebuilds_on_a_patch_byte_change(self):
        first = self._build()
        stamp = self.work / "stamps" / "gcc-16.2.0.installed"
        self.assertTrue(stamp.with_suffix(".installed.identity").is_file())
        self.assertIn(
            "mips-sgi-irix6.5-gcc (GCC) 16.2.0",
            (self.work / "stamps" / "gcc-16.2.0.installed.output").read_text(),
        )
        self.assertEqual((self.src / "test.txt").read_text(), "new\n")
        self.assertEqual(self._configure_count(), 1)
        self.assertEqual(self._make_count(), 2)
        identity_before = stamp.with_suffix(".installed.identity").read_text()

        second = self._build()
        self.assertIn("GCC 16.2.0 already installed", second.stdout)
        self.assertEqual(self._configure_count(), 1)
        self.assertEqual(self._make_count(), 2)

        self.patch.write_text(
            textwrap.dedent(
                """\
                --- a/test.txt
                +++ b/test.txt
                @@ -1 +1 @@
                -old
                +newer
                """
            )
        )
        self._build()
        self.assertEqual((self.src / "test.txt").read_text(), "newer\n")
        self.assertEqual(
            (self.src / ".irix-patched.d" / "0001-test.patch.applied").read_text(),
            self.patch.read_text(),
        )
        self.assertEqual(self._configure_count(), 2)
        self.assertEqual(self._make_count(), 4)
        identity_after = stamp.with_suffix(".installed.identity").read_text()
        self.assertNotEqual(identity_before, identity_after)

    def test_partial_and_missing_stamps_rebuild(self):
        self._build()
        stamp = self.work / "stamps" / "gcc-16.2.0.installed"
        stamp.with_suffix(".installed.output").unlink()
        self._build()
        self.assertEqual(self._configure_count(), 1)
        self.assertEqual(self._make_count(), 4)
        self.assertTrue(stamp.with_suffix(".installed.output").is_file())

        stamp.with_suffix(".installed.identity").unlink()
        self._build()
        self.assertEqual(self._configure_count(), 1)
        self.assertEqual(self._make_count(), 6)
        self.assertTrue(stamp.with_suffix(".installed.identity").is_file())

    BINUTILS_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        BINUTILS_VERSION=2.20.1
        BINUTILS_TARBALL="binutils-2.20.1.tar.bz2"
        BINUTILS_SHA256=deadbeef
        BINUTILS_RECIPE=pdaxrom
        BINUTILS_PATCHES=()
        IRIX_SYSROOT=
        TARGET=mips-sgi-irix6.5
        PREFIX="$2"
        STAMPS="$3"
        LOGS="$4"
        BUILD_DIR="$5"
        DOWNLOADS="$6"
        SRC_DIR="$7"
        FAKEBIN="$8"
        JOBS=1
        export PREFIX TARGET
        mkdir -p "$PREFIX/bin" "$STAMPS" "$LOGS" "$BUILD_DIR" "$DOWNLOADS" "$SRC_DIR/binutils-2.20.1"
        cat > "$SRC_DIR/binutils-2.20.1/configure" <<'EOS'
        #!/usr/bin/env bash
        set -euo pipefail
        : > config.status
        EOS
        chmod +x "$SRC_DIR/binutils-2.20.1/configure"
        export PATH="$FAKEBIN:$PATH"
        fetch() { :; }
        fetch_patches() { PATCH_FILES=(); }
        irix_apply_patches() { :; }
        build_binutils
        build_binutils
        """
    )

    BINUTILS_IDENTITY_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        TARGET=mips-sgi-irix6.5
        digest() { binutils_identity "$@" | irix_identity_digest; }
        BINUTILS_VERSION=2.47
        BINUTILS_SHA256=154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff
        BINUTILS_RECIPE=vanilla
        BINUTILS_PATCHES=()
        vanilla=$(digest --prefix=/p --target=mips-sgi-irix6.5)
        vanilla_again=$(digest --prefix=/p --target=mips-sgi-irix6.5)
        BINUTILS_VERSION=2.20.1
        BINUTILS_SHA256=71d37c96451333c5c0b84b170169fdcb138bbb27397dc06281905d9717c8ed64
        BINUTILS_RECIPE=pdaxrom
        BINUTILS_PATCHES=(
          "binutils-2.20.1-irix.diff:58ceeddf3ce3eda038a63f2b534d77bee540893619b67b06bfad095cef87ceee"
          "binutils-2.20.1-arm64-build-fix.diff:c932f55fce87bc8ac9735a3dc238c9bc614515c79f20a903c2a8b3b91398497f"
        )
        pdaxrom=$(digest --prefix=/p --target=mips-sgi-irix6.5)
        BINUTILS_PATCHES[0]="binutils-2.20.1-irix.diff:deadbeef"
        chunked=$(digest --prefix=/p --target=mips-sgi-irix6.5)
        BINUTILS_PATCHES=(
          "binutils-2.20.1-irix.diff:58ceeddf3ce3eda038a63f2b534d77bee540893619b67b06bfad095cef87ceee"
          "binutils-2.20.1-arm64-build-fix.diff:c932f55fce87bc8ac9735a3dc238c9bc614515c79f20a903c2a8b3b91398497f"
        )
        configure=$(digest --prefix=/q --target=mips-sgi-irix6.5)
        printf '%s\\n%s\\n%s\\n%s\\n%s\\n' \
          "$vanilla" "$vanilla_again" "$pdaxrom" "$chunked" "$configure"
        """
    )

    def test_binutils_identity_tracks_version_recipe_patches_and_options(self):
        result = run_bash(self.BINUTILS_IDENTITY_DRIVER, BUILD_TOOLCHAIN)
        vanilla, vanilla_again, pdaxrom, chunked, configure = result.stdout.split()
        self.assertEqual(vanilla, vanilla_again)
        self.assertEqual(
            len({vanilla, pdaxrom, chunked, configure}), 4
        )

    RECIPE_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        BINUTILS_VERSION=2.47
        resolve_binutils_recipe
        echo "2.47 ${BINUTILS_RECIPE} ${BINUTILS_TARBALL} ${#BINUTILS_PATCHES[@]} ${BINUTILS_SHA256}"
        BINUTILS_VERSION=2.20.1
        resolve_binutils_recipe
        echo "2.20.1 ${BINUTILS_RECIPE} ${BINUTILS_TARBALL} ${#BINUTILS_PATCHES[@]} ${BINUTILS_SHA256}"
        BINUTILS_VERSION=2.99
        if (resolve_binutils_recipe) 2>/dev/null; then echo "2.99 accepted"; else echo "2.99 refused"; fi
        """
    )

    def test_binutils_recipe_resolver_pins_both_releases(self):
        result = run_bash(self.RECIPE_DRIVER, BUILD_TOOLCHAIN)
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[0],
            "2.47 vanilla binutils-2.47.tar.xz 0 "
            "154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff",
        )
        self.assertEqual(
            lines[1],
            "2.20.1 pdaxrom binutils-2.20.1.tar.bz2 2 "
            "71d37c96451333c5c0b84b170169fdcb138bbb27397dc06281905d9717c8ed64",
        )
        self.assertEqual(lines[2], "2.99 refused")

    VANILLA_BINUTILS_DRIVER = textwrap.dedent(
        """\
        set -euo pipefail
        source "$1"
        BINUTILS_VERSION=2.47
        BINUTILS_TARBALL="binutils-2.47.tar.xz"
        BINUTILS_SHA256=deadbeef
        BINUTILS_RECIPE=vanilla
        BINUTILS_PATCHES=()
        IRIX_SYSROOT=
        TARGET=mips-sgi-irix6.5
        PREFIX="$2"
        STAMPS="$3"
        LOGS="$4"
        BUILD_DIR="$5"
        DOWNLOADS="$6"
        SRC_DIR="$7"
        FAKEBIN="$8"
        JOBS=1
        export PREFIX TARGET
        mkdir -p "$PREFIX/bin" "$STAMPS" "$LOGS" "$BUILD_DIR" "$DOWNLOADS" "$SRC_DIR/binutils-2.47"
        cat > "$SRC_DIR/binutils-2.47/configure" <<'EOS'
        #!/usr/bin/env bash
        set -euo pipefail
        : > config.status
        EOS
        chmod +x "$SRC_DIR/binutils-2.47/configure"
        export PATH="$FAKEBIN:$PATH"
        fetch() { :; }
        fetch_patches() { echo "fetch_patches called" >> "$FAKE_LOG"; }
        irix_apply_patches() { echo "irix_apply_patches called" >> "$FAKE_LOG"; }
        build_binutils
        build_binutils
        """
    )

    def test_vanilla_binutils_build_fetches_no_patches_and_stamps_the_recipe(self):
        self.work.mkdir(exist_ok=True)
        write_exe(
            self.fakebin / "make",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = install ]; then
                    cat > "$PREFIX/bin/mips-sgi-irix6.5-as" <<'EOS'
                #!/usr/bin/env bash
                echo "GNU assembler (GNU Binutils) 2.47"
                EOS
                    cat > "$PREFIX/bin/mips-sgi-irix6.5-ld" <<'EOS'
                #!/usr/bin/env bash
                echo "GNU ld (GNU Binutils) 2.47"
                EOS
                    chmod +x "$PREFIX/bin/mips-sgi-irix6.5-as" "$PREFIX/bin/mips-sgi-irix6.5-ld"
                fi
                """
            ),
        )
        env = dict(self.env)
        env["FAKE_LOG"] = str(self.tmp / "vanilla.log")
        result = run_bash(
            self.VANILLA_BINUTILS_DRIVER,
            BUILD_TOOLCHAIN,
            self.work / "prefix",
            self.work / "stamps",
            self.work / "logs",
            self.work / "build",
            self.work / "downloads",
            self.work / "src",
            self.fakebin,
            env=env,
        )
        self.assertEqual(result.stdout.count("binutils 2.47 already installed"), 1)
        self.assertFalse((self.tmp / "vanilla.log").exists())
        identity = (self.work / "stamps" / "binutils.installed.identity").read_text()
        self.assertIn("version=2.47", identity)
        self.assertIn("recipe=vanilla", identity)
        self.assertIn("patches:\nconfigure:", identity)

    def test_binutils_completion_record_captures_the_installed_outputs(self):
        self.work.mkdir(exist_ok=True)
        write_exe(
            self.fakebin / "make",
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = install ]; then
                    cat > "$PREFIX/bin/mips-sgi-irix6.5-as" <<'EOS'
                #!/usr/bin/env bash
                echo "GNU assembler (fake) 2.20.1"
                EOS
                    cat > "$PREFIX/bin/mips-sgi-irix6.5-ld" <<'EOS'
                #!/usr/bin/env bash
                echo "GNU ld (fake) 2.20.1"
                EOS
                    chmod +x "$PREFIX/bin/mips-sgi-irix6.5-as" "$PREFIX/bin/mips-sgi-irix6.5-ld"
                fi
                """
            ),
        )

        result = run_bash(
            self.BINUTILS_DRIVER,
            BUILD_TOOLCHAIN,
            self.work / "prefix",
            self.work / "stamps",
            self.work / "logs",
            self.work / "build",
            self.work / "downloads",
            self.work / "src",
            self.fakebin,
            env=self.env,
        )
        self.assertEqual(result.stdout.count("binutils 2.20.1 already installed"), 1)
        output = (self.work / "stamps" / "binutils.installed.output").read_text()
        self.assertIn("GNU assembler (fake) 2.20.1", output)
        self.assertIn("GNU ld (fake) 2.20.1", output)
        self.assertIn(str(self.work / "prefix" / "bin"), output)


class ScriptInvocationTest(TempDirTest):
    """Run the executor with fake curl so no request leaves the host."""

    def setUp(self):
        super().setUp()
        self.fakebin = self.tmp / "fakebin"
        self.curl_log = self.tmp / "curl.log"
        write_exe(
            self.fakebin / "curl",
            "#!/usr/bin/env bash\necho \"$*\" >> \"$FAKE_CURL_LOG\"\nexit 7\n",
        )
        # make is not part of every host image; the script only checks that
        # it exists before the conflict detection and fetch paths are probed.
        write_exe(self.fakebin / "make", "#!/usr/bin/env bash\nexit 0\n")
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.fakebin}{os.pathsep}{self.env['PATH']}"
        self.env["FAKE_CURL_LOG"] = str(self.curl_log)
        self.env["GCC_VERSION"] = ""
        self.env.pop("IRIX_WORK_ROOT", None)

    def _run(self, *args, work_root=None, cwd=None, script=None):
        env = dict(self.env)
        if work_root is not None:
            env["IRIX_WORK_ROOT"] = str(work_root)
        return subprocess.run(
            ["bash", str(script or BUILD_TOOLCHAIN), *map(str, args)],
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
        )

    def test_flake_work_root_picks_the_version_directory(self):
        work_root = self.tmp / "caller-tree" / ".scratch"
        result = self._run("--gcc", "15.3.0", work_root=work_root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"work dir: {work_root}/toolchain-15.3.0", result.stdout)
        self.assertTrue((work_root / "toolchain-15.3.0").is_dir())

    def test_direct_invocation_defaults_under_the_repo_scratch(self):
        fake_repo = self.tmp / "fake-repo"
        (fake_repo / "scripts" / "lib").mkdir(parents=True)
        shutil.copy(BUILD_TOOLCHAIN, fake_repo / "scripts" / "build-toolchain.sh")
        shutil.copy(LIB, fake_repo / "scripts" / "lib" / "build-identity.sh")
        result = self._run(
            "--gcc",
            "15.3.0",
            script=fake_repo / "scripts" / "build-toolchain.sh",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"work dir: {fake_repo}/.scratch/toolchain-15.3.0", result.stdout)
        self.assertTrue((fake_repo / ".scratch" / "toolchain-15.3.0").is_dir())

    def test_explicit_work_dir_beats_the_work_root(self):
        work_root = self.tmp / "caller-tree" / ".scratch"
        explicit = self.tmp / "explicit"
        result = self._run(
            "--gcc", "15.3.0", "--work-dir", explicit, work_root=work_root
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"work dir: {explicit}", result.stdout)
        self.assertFalse(work_root.exists())

    def test_another_release_in_the_work_dir_is_refused_before_fetching(self):
        work = self.tmp / "shared"
        (work / "src" / "gcc-16.2.0").mkdir(parents=True)
        result = self._run("--gcc", "15.3.0", "--work-dir", work)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another GCC release", result.stderr)
        self.assertIn("--clean", result.stderr)
        self.assertIn("toolchain-15.3.0", result.stderr)
        self.assertFalse(self.curl_log.exists(), "fetch was attempted before the conflict check")

    def test_same_release_artefacts_do_not_conflict(self):
        work = self.tmp / "shared"
        (work / "src" / "gcc-15.3.0").mkdir(parents=True)
        result = self._run("--gcc", "15.3.0", "--work-dir", work)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("another GCC release", result.stderr)
        self.assertTrue(self.curl_log.exists(), "fetch was never attempted")

    def test_binutils_selector_defaults_to_vanilla_247(self):
        work = self.tmp / "work"
        result = self._run("--gcc", "15.3.0", "--work-dir", work)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binutils: 2.47 (vanilla recipe)", result.stdout)

    def test_binutils_selector_accepts_the_fallback_recipe(self):
        work = self.tmp / "work"
        result = self._run(
            "--binutils", "2.20.1", "--gcc", "15.3.0", "--work-dir", work
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binutils: 2.20.1 (pdaxrom recipe)", result.stdout)

    def test_binutils_selector_refuses_unknown_versions_before_fetching(self):
        refused = self._run("--binutils", "2.99", "--work-dir", self.tmp / "other")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("unsupported binutils version: 2.99", refused.stderr)
        self.assertFalse(self.curl_log.exists(), "fetch ran before the version check")

    def test_prefix_holding_another_release_is_refused(self):
        prefix = self.tmp / "prefix"
        write_exe(
            prefix / "bin" / "mips-sgi-irix6.5-gcc",
            "#!/usr/bin/env bash\necho 'mips-sgi-irix6.5-gcc (GCC) 15.2.0'\n",
        )
        work = self.tmp / "work"
        result = self._run(
            "--gcc", "16.2.0", "--work-dir", work, "--prefix", prefix
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reports", result.stderr)
        self.assertIn("15.2.0", result.stderr)
        self.assertFalse(self.curl_log.exists())


if __name__ == "__main__":
    unittest.main()
