#!/usr/bin/env python3
"""Host-only test scaffolding: a temporary rig and toolchain double (issue #5).

scripts/smoke/test-smoke.py and scripts/runtime/test-rebuild-libc.py drive
the fail-closed guest transaction against a temporary IRIX_RIG_DIR. lib.sh
derives RIG_IRIS_DIR, RIG_IRIS_CI and the guest lock from that root, so the
committed fake scripts/rig/fake-iris-ci.sh is copied to
<root>/iris/target/release/iris-ci and every iris-ci call lands there. The
fake keeps its "guest" filesystem under <root>/state and is configured per
test through the FAKE_* environment described in fake-iris-ci.sh.

Nothing here touches the live rig, the guest, nix, a compiler, an emulator or
the network; the socket is a real Unix socket under the temporary root.
"""

import os
import shutil
import socket
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FAKE_IRIS_CI = REPO_ROOT / "scripts" / "rig" / "fake-iris-ci.sh"


class FakeRig:
    """A temporary rig tree with fake iris and iris-ci binaries."""

    def __init__(self, root):
        self.root = Path(root)
        release = self.root / "iris" / "target" / "release"
        release.mkdir(parents=True, exist_ok=True)
        self.state = self.root / "state"
        self.state.mkdir(exist_ok=True)
        self.call_log = self.root / "calls.log"

        iris_ci = release / "iris-ci"
        shutil.copy(FAKE_IRIS_CI, iris_ci)
        iris_ci.chmod(0o755)
        iris = release / "iris"
        iris.write_text("#!/usr/bin/env bash\nexit 0\n")
        iris.chmod(0o755)

        # A bound Unix socket file survives close() and satisfies lib.sh's
        # `[ -S "$RIG_SOCKET" ]` check; ping is the fake iris-ci's answer.
        self.socket = self.root / "iris.sock"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket))
        sock.close()

    def env(self, **overrides):
        """The process environment pinned to this root plus FAKE_* overrides."""
        env = dict(os.environ)
        env.pop("RIG_GUEST_LOCK_HELD", None)
        env.pop("IRIS_SOCKET", None)
        env.update(
            {
                "IRIX_RIG_DIR": str(self.root),
                "RIG_GUEST_LOCK_TIMEOUT": "10",
                "FAKE_IRIS_STATE": str(self.state),
                "FAKE_CALL_LOG": str(self.call_log),
            }
        )
        env.update({key: str(value) for key, value in overrides.items()})
        return env

    def calls(self):
        """Every iris-ci argv line the fake recorded."""
        if not self.call_log.exists():
            return []
        return [line for line in self.call_log.read_text().splitlines() if line]

    def transport_calls(self):
        """Recorded calls that carry --timeout (run/put/get, not ping)."""
        return [line for line in self.calls() if "--timeout" in line]


def write_fake_toolchain(prefix, sysroot):
    """A fake cross prefix: gcc compiles/links stubs, readelf prints INTERP.

    Failure seams: FAKE_GCC_FAIL=compile|link|all and FAKE_READELF_FAIL=1
    (exit non-zero) or FAKE_READELF_NO_INTERP=1 (exit zero, no INTERP) let a
    test reach the guest transaction or stop before it.
    """
    prefix = Path(prefix)
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    gcc = bin_dir / "mips-sgi-irix6.5-gcc"
    gcc.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            if [ "${{1:-}}" = "-print-sysroot" ]; then
                printf '%s\\n' "{sysroot}"
                exit 0
            fi
            compile=0
            out=
            prev=
            for arg in "$@"; do
                [ "$prev" != "-o" ] || out=$arg
                [ "$arg" != "-c" ] || compile=1
                prev=$arg
            done
            fail=${{FAKE_GCC_FAIL:-}}
            if [ "$compile" = 1 ]; then
                if [ "$fail" = compile ] || [ "$fail" = all ]; then exit 1; fi
            else
                if [ "$fail" = link ] || [ "$fail" = all ]; then exit 1; fi
            fi
            [ -n "$out" ] || exit 1
            printf 'fake object or binary\\n' >"$out"
            """
        )
    )
    gcc.chmod(0o755)

    readelf = bin_dir / "mips-sgi-irix6.5-readelf"
    readelf.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -eu
            [ "${FAKE_READELF_FAIL:-0}" != 1 ] || exit 1
            if [ "${FAKE_READELF_NO_INTERP:-0}" = 1 ]; then
                echo "ELF header only"
            else
                echo "  INTERP         0x1"
            fi
            """
        )
    )
    readelf.chmod(0o755)
    return prefix
