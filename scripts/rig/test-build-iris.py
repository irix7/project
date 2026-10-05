#!/usr/bin/env python3
"""Host-only tests for scripts/rig/build-iris.sh's build receipt (issue #26).

The script is driven with a local fake git origin, a fake rig-local Rust
toolchain and a fake nix on PATH, so nothing is fetched from the network and
no Rust code is compiled. The receipt fields, the dirty-checkout refusal and
--force/--force-checkout are checked directly.

Run: python3 scripts/rig/test-build-iris.py
"""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "rig" / "build-iris.sh"

FAKE_CARGO_VERSION = "cargo 1.99.0-nightly (fake 2026-10-05)"
FAKE_RUSTC_VERSION = "rustc 1.99.0-nightly (fake 2026-10-05)"
FAKE_NIX = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    echo "$*" >> "$FAKE_NIX_LOG"
    if [ -n "${FAKE_NIX_FAIL:-}" ]; then
        echo "fake nix: failing on request" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$RIG_IRIS")"
    printf '#!/usr/bin/env bash\\necho "iris (fake)"\\n' > "$RIG_IRIS"
    printf '#!/usr/bin/env bash\\necho "iris-ci (fake)"\\n' > "$RIG_IRIS_CI"
    chmod +x "$RIG_IRIS" "$RIG_IRIS_CI"
    """
)


def write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


class BuildIrisTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="build-iris-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

        self.origin = self.tmp / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "main")
        (self.origin / ".gitignore").write_text("target/\n")
        (self.origin / "README.md").write_text("iris\n")
        git(self.origin, "add", ".")
        git(self.origin, "commit", "-q", "-m", "initial")
        self.commit = git(self.origin, "rev-parse", "HEAD")

        self.rig = self.tmp / "rig"
        self.iris_dir = self.rig / "iris"
        self.receipt = self.rig / "state" / "build-iris.receipt"
        self.nix_log = self.tmp / "nix.log"

        self.rust_bin = (
            self.rig
            / "tools"
            / "rustup"
            / "toolchains"
            / "nightly-x86_64-unknown-linux-gnu"
            / "bin"
        )
        write_exe(
            self.rust_bin / "cargo",
            f"#!/usr/bin/env bash\necho '{FAKE_CARGO_VERSION}'\n",
        )
        write_exe(
            self.rust_bin / "rustc",
            f"#!/usr/bin/env bash\necho '{FAKE_RUSTC_VERSION}'\n",
        )

        self.fakebin = self.tmp / "fakebin"
        write_exe(self.fakebin / "nix", FAKE_NIX)

    def _env(self, **extra):
        env = os.environ.copy()
        env["PATH"] = f"{self.fakebin}{os.pathsep}{env['PATH']}"
        env["IRIX_RIG_DIR"] = str(self.rig)
        env["RIG_IRIS_REPO"] = str(self.origin)
        env["RIG_IRIS_COMMIT"] = self.commit
        env["FAKE_NIX_LOG"] = str(self.nix_log)
        env.pop("IRIX_RUSTUP_HOME", None)
        env.pop("IRIX_CARGO_HOME", None)
        env.update(extra)
        return env

    def run_script(self, *args, env=None):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env or self._env(),
        )

    def nix_calls(self):
        return len(self.nix_log.read_text().splitlines()) if self.nix_log.exists() else 0

    def build_once(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_first_build_writes_a_receipt_and_the_second_reuses_it(self):
        self.build_once()
        self.assertTrue((self.iris_dir / "target" / "release" / "iris").is_file())
        self.assertTrue((self.iris_dir / "target" / "release" / "iris-ci").is_file())
        self.assertEqual(self.nix_calls(), 1)

        fields = dict(
            line.split("=", 1)
            for line in self.receipt.read_text().splitlines()
            if "=" in line
        )
        self.assertEqual(fields["repo"], str(self.origin))
        self.assertEqual(fields["requested_commit"], self.commit)
        self.assertEqual(
            fields["resolved_commit"], git(self.iris_dir, "rev-parse", "HEAD")
        )
        self.assertEqual(fields["features"], "lightning")
        self.assertEqual(fields["rustc"], FAKE_RUSTC_VERSION)
        self.assertEqual(fields["cargo"], FAKE_CARGO_VERSION)
        self.assertEqual(fields["iris"], str(self.iris_dir / "target" / "release" / "iris"))
        self.assertEqual(
            fields["iris_ci"], str(self.iris_dir / "target" / "release" / "iris-ci")
        )

        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already built", second.stdout)
        self.assertEqual(self.nix_calls(), 1)

    def test_changed_pin_invalidates_the_binary(self):
        self.build_once()
        (self.origin / "README.md").write_text("iris, second commit\n")
        git(self.origin, "add", ".")
        git(self.origin, "commit", "-q", "-m", "second")
        new_commit = git(self.origin, "rev-parse", "HEAD")

        result = self.run_script(env=self._env(RIG_IRIS_COMMIT=new_commit))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.nix_calls(), 2)
        self.assertIn(f"requested_commit={new_commit}", self.receipt.read_text())
        self.assertEqual(
            git(self.iris_dir, "rev-parse", "HEAD"),
            new_commit,
        )

    def test_changed_features_invalidate_the_binary(self):
        self.build_once()
        text = self.receipt.read_text().replace(
            "features=lightning", "features=lightning,extra"
        )
        self.receipt.write_text(text)

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.nix_calls(), 2)
        self.assertIn("features=lightning\n", self.receipt.read_text())

    def test_changed_rust_identity_invalidates_the_binary(self):
        self.build_once()
        write_exe(
            self.rust_bin / "cargo",
            "#!/usr/bin/env bash\necho 'cargo 2.0.0-nightly (fake 2026-10-06)'\n",
        )

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.nix_calls(), 2)

    def test_force_rebuilds_even_when_the_receipt_matches(self):
        self.build_once()
        result = self.run_script("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.nix_calls(), 2)

    def test_dirty_checkout_is_refused_without_force_checkout(self):
        self.build_once()
        (self.iris_dir / "README.md").write_text("local emulator work\n")

        result = self.run_script("--force")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local changes", result.stderr)
        self.assertIn("--force-checkout", result.stderr)
        self.assertEqual(
            (self.iris_dir / "README.md").read_text(), "local emulator work\n"
        )
        self.assertEqual(self.nix_calls(), 1)

    def test_force_checkout_explicitly_discards_local_changes(self):
        self.build_once()
        (self.iris_dir / "README.md").write_text("local emulator work\n")

        result = self.run_script("--force", "--force-checkout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.iris_dir / "README.md").read_text(), "iris\n")
        self.assertEqual(self.nix_calls(), 2)

    def test_failed_build_leaves_no_receipt_and_rebuilds_next_time(self):
        failed = self.run_script(env=self._env(FAKE_NIX_FAIL="1"))
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(self.nix_calls(), 1)

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.receipt.is_file())
        self.assertEqual(self.nix_calls(), 2)

    def test_help_documents_both_flags(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--force-checkout", result.stdout)
        self.assertIn("--force", result.stdout)


if __name__ == "__main__":
    unittest.main()
