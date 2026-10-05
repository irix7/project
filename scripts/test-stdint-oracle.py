#!/usr/bin/env python3
"""Host-only tests for scripts/test-stdint-oracle.sh (issue #30).

The oracle comparison itself is guest evidence and runs against the live rig
with a licensed MIPSpro; it is deliberately not run here. These cases drive
the script against a temporary IRIX_RIG_DIR with a fake iris-ci, a fake cross
compiler and a real Unix socket, and cover probe generation, the per-ABI
shape checks and diff, and the skip/fail classification. Nothing touches the
live rig, the guest, nix or the network.

Run: python3 scripts/test-stdint-oracle.py
"""

import os
import re
import socket
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ORACLE = REPO_ROOT / "scripts" / "test-stdint-oracle.sh"
HOSTED = REPO_ROOT / "scripts" / "test-hosted-stdint.sh"
PROBE = REPO_ROOT / "oracle" / "stdint-probe.c"

# The one independently authored IRIX o32/n32 model the probe must print, read
# from the same file the script reads so the shell and Python sides cannot
# drift.
EXPECTED = (REPO_ROOT / "scripts" / "stdint-oracle.model").read_text()

GCC_STUB = """\
#!/usr/bin/env bash
# Fake cross compiler: writes something to the -o argument and succeeds.
set -euo pipefail
out=
want=0
for arg in "$@"; do
	if [ "$want" = 1 ]; then out=$arg; want=0; continue; fi
	case "$arg" in
		-o) want=1 ;;
	esac
done
[ -n "$out" ] || exit 2
printf 'not a real binary\\n' >"$out"
"""

READELF_STUB = """\
#!/usr/bin/env bash
printf 'INTERP\\n'
"""

IC_STUB = '''\
#!/usr/bin/env python3
"""Fake iris-ci for the stdint oracle tests.

Maps guest absolute paths under FAKE_ORACLE_STATE/guest and fabricates the
files the guest runner would write, driven by FAKE_ORACLE_MODE.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

state = Path(os.environ["FAKE_ORACLE_STATE"])
guest_root = state / "guest"
record = state / "record"
mode = os.environ.get("FAKE_ORACLE_MODE", "pass")
cross_out = os.environ.get("FAKE_CROSS_OUT", "")
native_out = os.environ.get("FAKE_NATIVE_OUT", "")

args = sys.argv[1:]
record.parent.mkdir(parents=True, exist_ok=True)
with record.open("a") as fh:
    fh.write("argv: " + " ".join(args) + "\\n")

pos = []
i = 0
while i < len(args):
    arg = args[i]
    if arg in ("--socket", "--timeout"):
        i += 2
        continue
    if arg == "-q":
        i += 1
        continue
    pos.append(arg)
    i += 1

if not pos:
    sys.exit(0)
op = pos[0]


def guest_path(path):
    return Path(str(guest_root) + path)


def write(gpath, text):
    target = guest_path(gpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


if op == "ping":
    sys.exit(0)

if op == "put":
    if mode == "transport-put":
        sys.exit(1)
    dest = pos[pos.index("--to") + 1]
    if dest.endswith("stdint-oracle-run.sh"):
        # The runner is never executed by the fake; at least prove it parses.
        check = subprocess.run(["sh", "-n", pos[1]], capture_output=True, text=True)
        if check.returncode != 0:
            sys.stderr.write(check.stderr)
            sys.exit(check.returncode)
    target = guest_path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pos[1], target)
    sys.exit(0)

if op == "get":
    src = guest_path(pos[1])
    if not src.exists():
        sys.exit(1)
    dest = Path(pos[pos.index("--to") + 1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    sys.exit(0)

if op == "run":
    cmd = pos[1]
    if "echo stdint-oracle-ready" in cmd:
        sys.exit(1 if mode == "no-login" else 0)
    match = re.search(r"mkdir -p (\\S+)", cmd)
    if match:
        guest_path(match.group(1)).mkdir(parents=True, exist_ok=True)
        sys.exit(0)
    match = re.search(r"rm -rf (\\S+)", cmd)
    if match:
        shutil.rmtree(guest_path(match.group(1)), ignore_errors=True)
        sys.exit(0)
    if "stdint-oracle-run.sh" in cmd:
        match = re.search(r"stdint-oracle-run\\.sh (\\S+)", cmd)
        directory = match.group(1)
        for abi in ("o32", "n32"):
            write(f"{directory}/probe.gcc.{abi}.stdout", cross_out)
            write(f"{directory}/probe.gcc.{abi}.stderr", "")
            write(f"{directory}/probe.gcc.{abi}.status", "0")
            write(f"{directory}/probe.cc.{abi}.compile", "")
            if mode == "no-cc":
                write(f"{directory}/probe.cc.{abi}.unavailable", "yes\\n")
                write(f"{directory}/probe.cc.{abi}.stdout", "")
                write(f"{directory}/probe.cc.{abi}.stderr", "")
                write(f"{directory}/probe.cc.{abi}.status", "127")
            else:
                write(f"{directory}/probe.cc.{abi}.unavailable", "no\\n")
                write(f"{directory}/probe.cc.{abi}.stdout", native_out)
                write(f"{directory}/probe.cc.{abi}.stderr", "")
                write(
                    f"{directory}/probe.cc.{abi}.status",
                    "1" if mode == "native-nonzero" else "0",
                )
        write(f"{directory}/txn.status", "0")
        sys.exit(0)
    sys.exit(0)

sys.exit(0)
'''


class OracleHarness(unittest.TestCase):
    """A temp rig tree, fake tools and an environment pinned to the temp root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stdint-oracle-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.rig = self.root / "rig"
        (self.rig / "iris" / "target" / "release").mkdir(parents=True)
        self.iris = self.rig / "iris" / "target" / "release" / "iris"
        self.iris_ci = self.rig / "iris" / "target" / "release" / "iris-ci"
        self.iris.write_text("#!/bin/sh\nexit 0\n")
        self.iris.chmod(0o755)
        self.socket = self.rig / "iris.sock"
        self.state = self.root / "state"
        self.state.mkdir()

        self.prefix = self.root / "prefix"
        (self.prefix / "bin").mkdir(parents=True)
        self.sysroot = self.root / "sysroot"
        (self.sysroot / "usr" / "include").mkdir(parents=True)

        self.write_fake_tool(self.prefix / "bin" / "mips-sgi-irix6.5-gcc", GCC_STUB)
        self.write_fake_tool(self.prefix / "bin" / "mips-sgi-irix6.5-readelf", READELF_STUB)
        self.write_fake_tool(self.iris_ci, IC_STUB)

        self.sock = self.bind_socket()

        self.env = dict(os.environ)
        self.env.update(
            {
                "IRIX_RIG_DIR": str(self.rig),
                "RIG_GUEST_LOCK_TIMEOUT": "10",
                "FAKE_ORACLE_STATE": str(self.state),
                "FAKE_CROSS_OUT": EXPECTED,
                "FAKE_NATIVE_OUT": EXPECTED,
            }
        )
        self.env.pop("RIG_GUEST_LOCK_HELD", None)
        self.env.pop("IRIS_SOCKET", None)

    @staticmethod
    def write_fake_tool(path, text):
        path.write_text(textwrap.dedent(text))
        path.chmod(0o755)

    def bind_socket(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket))
        self.addCleanup(sock.close)
        return sock

    def run_oracle(self, *args, timeout=30):
        return subprocess.run(
            [
                str(ORACLE),
                "--prefix",
                str(self.prefix),
                "--sysroot",
                str(self.sysroot),
                *args,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def record(self):
        path = self.state / "record"
        return path.read_text() if path.exists() else ""

    def unlink_socket(self):
        self.sock.close()
        os.unlink(self.socket)


class PassCase(OracleHarness):
    def test_matching_outputs_pass(self):
        result = self.run_oracle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pass", result.stdout)
        self.assertTrue((self.rig / "guest.lock").exists())

    def test_guest_work_is_namespaced_and_socket_is_addressed(self):
        result = self.run_oracle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = self.record()
        self.assertRegex(record, r"/tmp/stdint-oracle-\d+-\d+/")
        self.assertIn(f"--socket {self.socket}", record)

    def test_both_abis_are_run_on_both_compilers(self):
        result = self.run_oracle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record = self.record()
        for token in (
            "probe.gcc.o32",
            "probe.gcc.n32",
            "probe.cc.o32",
            "probe.cc.n32",
        ):
            self.assertIn(token, record)


class MismatchCase(OracleHarness):
    def test_differing_line_fails_and_is_reported(self):
        self.env["FAKE_CROSS_OUT"] = EXPECTED.replace(
            "INT32_MAX=2147483647", "INT32_MAX=2147483648"
        )
        result = self.run_oracle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the MIPSpro oracle", result.stderr)
        self.assertIn("INT32_MAX=2147483648", result.stderr)

    def test_truncated_cross_output_fails_the_shape_check(self):
        self.env["FAKE_CROSS_OUT"] = EXPECTED.replace("SIZE_MAX=4294967295\n", "")
        result = self.run_oracle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected 16 result lines", result.stderr)

    def test_unnamed_cross_line_fails_the_shape_check(self):
        self.env["FAKE_CROSS_OUT"] = EXPECTED.replace(
            "sizeof(void*)=4", "sizeof(long)=4"
        )
        result = self.run_oracle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected key sizeof(void*)", result.stderr)


class OracleSanityCase(OracleHarness):
    def test_broken_native_model_fails_before_the_diff(self):
        self.env["FAKE_NATIVE_OUT"] = EXPECTED.replace(
            "SIZE_MAX=4294967295", "SIZE_MAX=4294967294"
        )
        result = self.run_oracle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected IRIX integer model", result.stderr)


class FailureClassificationCase(OracleHarness):
    def test_transport_failure_fails_closed(self):
        self.env["FAKE_ORACLE_MODE"] = "transport-put"
        result = self.run_oracle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not complete", result.stderr)

    def test_nonzero_native_exit_fails(self):
        self.env["FAKE_ORACLE_MODE"] = "native-nonzero"
        result = self.run_oracle()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MIPSpro o32 probe exited 1", result.stderr)


class SkipCase(OracleHarness):
    def test_guest_login_unavailable_skips(self):
        self.env["FAKE_ORACLE_MODE"] = "no-login"
        result = self.run_oracle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip:", result.stdout)
        self.assertIn("guest login did not answer", result.stdout)

    def test_mipspro_cc_unavailable_skips(self):
        self.env["FAKE_ORACLE_MODE"] = "no-cc"
        result = self.run_oracle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip:", result.stdout)
        self.assertIn("MIPSpro cc is unavailable", result.stdout)

    def test_rig_down_skips(self):
        self.unlink_socket()
        result = self.run_oracle()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip:", result.stdout)
        self.assertIn("rig is not running", result.stdout)

    def test_missing_prefix_skips(self):
        result = subprocess.run(
            [str(ORACLE), "--prefix", str(self.root / "nonexistent")],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip:", result.stdout)
        self.assertIn("toolchain prefix not found", result.stdout)

    def test_prefix_without_cross_skips(self):
        bare = self.root / "bare-prefix"
        (bare / "bin").mkdir(parents=True)
        result = subprocess.run(
            [
                str(ORACLE),
                "--prefix",
                str(bare),
                "--sysroot",
                str(self.sysroot),
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip:", result.stdout)
        self.assertIn("cross compiler not found", result.stdout)


class ArgumentsTest(unittest.TestCase):
    def test_help_prints_usage(self):
        result = subprocess.run(
            [str(ORACLE), "--help"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Usage: scripts/test-stdint-oracle.sh", result.stdout)
        for flag in ("--prefix", "--sysroot", "--timeout"):
            self.assertIn(flag, result.stdout)

    def test_unknown_option_fails(self):
        result = subprocess.run(
            [str(ORACLE), "--nope"], capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option", result.stderr)


class ProbeSourceTest(unittest.TestCase):
    def test_probe_measures_the_required_model(self):
        text = PROBE.read_text()
        for key in (
            "sizeof(int8_t)",
            "sizeof(int16_t)",
            "sizeof(int32_t)",
            "sizeof(int64_t)",
            "sizeof(uint8_t)",
            "sizeof(uint16_t)",
            "sizeof(uint32_t)",
            "sizeof(uint64_t)",
            "sizeof(intptr_t)",
            "sizeof(uintptr_t)",
            "sizeof(intmax_t)",
            "sizeof(uintmax_t)",
            "sizeof(void*)",
            "INT32_MAX",
            "UINT64_MAX",
            "SIZE_MAX",
        ):
            self.assertIn(key, text)

    def test_probe_supplies_size_max_itself(self):
        text = PROBE.read_text()
        self.assertIn("#ifndef SIZE_MAX", text)
        self.assertIn("#define SIZE_MAX ((size_t)-1)", text)

    def test_probe_includes_only_standard_headers(self):
        allowed = {"stdio.h", "inttypes.h", "stdint.h"}
        for line in PROBE.read_text().splitlines():
            match = re.match(r"#\s*include\s+<([^>]+)>", line.strip())
            if match:
                self.assertIn(match.group(1), allowed)


class HostedStdintCxxSkipTest(unittest.TestCase):
    """Probe C's skip names the exact recipe for a C++-capable prefix."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cxx-skip-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "prefix"
        (self.prefix / "bin").mkdir(parents=True)
        gcc = self.prefix / "bin" / "mips-sgi-irix6.5-gcc"
        gcc.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                for arg in "$@"; do
                	case "$arg" in
                		-print-sysroot) echo ""; exit 0 ;;
                		-print-prog-name=cc1plus) echo "cc1plus"; exit 0 ;;
                	esac
                done
                exit 0
                """
            )
        )
        gcc.chmod(0o755)

    def test_skip_message_states_the_recipe(self):
        result = subprocess.run(
            [str(HOSTED), "--prefix", str(self.prefix)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip: C C++ probes", result.stdout)
        self.assertIn("--languages c,c++", result.stdout)
        self.assertIn("all-gcc install-gcc", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
