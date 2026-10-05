#!/usr/bin/env python3
"""Drive the fresh IRIX 6.5.7m bring-up over the iris CI socket (issue #2).

The driver talks the plain `iris --ci` protocol (newline-delimited JSON over a
Unix socket) rather than shelling out to `iris-ci`, because the install needs
prompt-aware reactions: inst asks for CDs by name, fx walks nested menus, and
the PROM console disappears behind a graphical console if NVRAM is stale.

Phases, run in order against a running emulator started by provision-guest.sh:

  phase-a   seed NVRAM (SystemPartition, OSLoadPartition, console=d) headless
  label     boot fx.ARCS from the install CD and label/create the boot disk
  install   miniroot install from the four media discs, then restart into IRIX
  verify    log in on the console and record uname -a / hinv as evidence

Every byte the guest emits is appended to the rig's serial log by iris itself
(`--serial-log`); this driver additionally mirrors it to the driver log so a
post-mortem can see exactly which prompt it was looking at.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time

POLL_SECS = 0.5
DEFAULT_TIMEOUT = 120.0

SYSTEM_PARTITION = "scsi(0)disk(1)rdisk(0)partition(8)"
OSLOAD_PARTITION = "scsi(0)disk(1)rdisk(0)partition(0)"
FX_BOOT = "boot -f dksc(0,4,8)sashARCS dksc(0,4,7)stand/fx.ARCS --x"

# Release words that carry no disc identity; dropped from match words.
DISC_STOPWORDS = {"irix", "6", "5", "7", "and"}


def _disc_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _disc_index(tokens: list[str]) -> str | None:
    """The disc's own index, as inst and the filenames spell it.

    Handles `(1 of 2)`, `1-of-2` and trailing forms like `FOUNDATION-1`; a
    lone `02/00` date stamp is ignored because the number before "of" wins.
    """
    for i, token in enumerate(tokens):
        if token == "of" and i > 0 and tokens[i - 1].isdigit():
            return tokens[i - 1]
    numbers = [t for t in tokens if t.isdigit()]
    return numbers[-1] if numbers else None


def disc_matches(requested: str, mounted: str) -> bool:
    """True when `mounted` is the disc inst asked for by `requested`.

    inst asks for `IRIX 6.5.7 Installation Tools and Overlays 1-of-2 02/00`
    while the changer holds `IRIX 6.5.7 Installation Tools and Overlays (1 of
    2).iso`. The disc's words (release words and pure numbers dropped) must
    all appear in the filename, and both must name the same disc index —
    otherwise "overlays 2" would match the "1 of 2" disc.
    """
    req_tokens = _disc_tokens(requested)
    name_tokens = _disc_tokens(mounted)
    words = [t for t in req_tokens if not t.isdigit() and t not in DISC_STOPWORDS]
    if not all(w in name_tokens for w in words):
        return False
    req_index, name_index = _disc_index(req_tokens), _disc_index(name_tokens)
    if req_index is None or name_index is None:
        return False
    return req_index == name_index


class RigError(Exception):
    pass


class Rig:
    def __init__(self, socket_path: str, log_path: str | None = None, echo: bool = True):
        self.socket_path = socket_path
        self.buf = ""
        self.echo = echo
        self.logf = open(log_path, "a", buffering=1) if log_path else None

    def close(self) -> None:
        if self.logf:
            self.logf.close()
            self.logf = None

    def ic_command(self, ic: str, *args: str) -> list[str]:
        """The iris-ci argv that names this rig's socket explicitly.

        `--socket` is a global iris-ci option: without it the CLI falls back to
        $IRIS_SOCKET or the default /tmp/iris.sock, which would address the
        sibling session (ADR-0004).
        """
        return [ic, "--socket", self.socket_path, *args]

    def ic_env(self) -> dict[str, str]:
        """The iris-ci environment, pinned to this rig's socket.

        Both carriers are set deliberately: argv survives an inherited
        environment, and the environment covers a CLI that ignores argv order.
        """
        env = dict(os.environ)
        env["IRIS_SOCKET"] = self.socket_path
        return env

    def run_ic(self, ic: str, *args: str, **kwargs):
        """Run iris-ci with the selected socket in argv and the environment."""
        kwargs.setdefault("check", True)
        kwargs.setdefault("env", self.ic_env())
        return subprocess.run(self.ic_command(ic, *args), **kwargs)

    def _log(self, text: str) -> None:
        if self.logf:
            self.logf.write(text)

    def _trace(self, text: str) -> None:
        if self.echo and text:
            sys.stdout.write(text)
            sys.stdout.flush()

    def rpc(self, cmd: str, timeout: float = 300.0, **args):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.socket_path)
        except OSError as e:
            raise RigError(f"cannot connect to CI socket {self.socket_path}: {e}") from e
        try:
            stream = sock.makefile("rb")
            line = json.dumps({"cmd": cmd, "args": args}).encode() + b"\n"
            sock.sendall(line)
            reply = stream.readline()
            if not reply:
                raise RigError(f"{cmd}: socket closed with no reply")
            resp = json.loads(reply)
            if not resp.get("ok"):
                raise RigError(f"{cmd}: {resp.get('error')}")
            return resp.get("data")
        finally:
            sock.close()

    def pump(self) -> None:
        data = self.rpc("serial-read", timeout=60.0)
        if data:
            self.buf += data
            self._log(data)
            self._trace(data)

    def sync(self) -> None:
        for _ in range(4):
            data = self.rpc("serial-read", timeout=60.0)
            if not data:
                return
            self._log(data)
            self._trace(data)

    def clear(self) -> None:
        self.buf = ""

    def send(self, text: str, cr: bool = True) -> None:
        self.rpc("serial-send", data=text + ("\r" if cr else ""))

    def expect(self, patterns, timeout: float = DEFAULT_TIMEOUT):
        if isinstance(patterns, str):
            patterns = [patterns]
        deadline = time.monotonic() + timeout
        while True:
            for pattern in patterns:
                idx = self.buf.find(pattern)
                if idx >= 0:
                    end = idx + len(pattern)
                    matched, self.buf = self.buf[:end], self.buf[end:]
                    return matched, pattern
            if time.monotonic() >= deadline:
                tail = self.buf[-3000:]
                self.buf = ""
                raise RigError(
                    f"timed out after {timeout:.0f}s waiting for {patterns!r}; last output:\n{tail}"
                )
            self.pump()
            time.sleep(POLL_SECS)

    def expect_re(self, regex: str, timeout: float = DEFAULT_TIMEOUT):
        rx = re.compile(regex)
        deadline = time.monotonic() + timeout
        while True:
            m = rx.search(self.buf)
            if m:
                end = m.end()
                matched, self.buf = self.buf[:end], self.buf[end:]
                return matched, m
            if time.monotonic() >= deadline:
                tail = self.buf[-3000:]
                self.buf = ""
                raise RigError(
                    f"timed out after {timeout:.0f}s waiting for /{regex}/; last output:\n{tail}"
                )
            self.pump()
            time.sleep(POLL_SECS)

    def send_expect(self, text: str, patterns, timeout: float = DEFAULT_TIMEOUT, cr: bool = True):
        self.send(text, cr=cr)
        return self.expect(patterns, timeout)


def mark(state_dir: str, name: str) -> None:
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, f"{name}.done"), "w") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S\n"))


def marked(state_dir: str, name: str) -> bool:
    return os.path.exists(os.path.join(state_dir, f"{name}.done"))


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


def enter_prom_monitor(rig: Rig, timeout: float = 300.0) -> None:
    """Get from wherever the PROM is to the `>>` command monitor.

    A freshly reinitialised NVRAM has AutoLoad=Yes and a blank disk, so the
    first thing the PROM does is fail to load sash and stop on a keypress.
    Depending on how far the boot got, the maintenance menu may already be up.
    """
    out, pattern = rig.expect([">> ", "Option?", "Unable to boot"], timeout)
    if pattern == "Unable to boot":
        rig.send("")
        out, pattern = rig.expect([">> ", "Option?"], 60)
    if pattern == "Option?":
        rig.send("5")
        rig.expect([">> "], 60)


def phase_a(rig: Rig, state_dir: str) -> None:
    """Seed the PROM environment in a headless boot, then persist NVRAM.

    With the graphics device mapped and no `console` variable, the PROM talks
    to the framebuffer and the serial channel is dead-eared, so this phase runs
    headless (--headless on the iris command line) to make serial the only
    console. `console=d` then keeps serial as the console for every later boot.
    """
    rig.rpc("start")
    enter_prom_monitor(rig)

    for cmd in (
        f"setenv -f SystemPartition {SYSTEM_PARTITION}",
        f"setenv -f OSLoadPartition {OSLOAD_PARTITION}",
        "setenv -f console d",
    ):
        out, _ = rig.send_expect(cmd, [">> "], 60)
        print(f"  $ {cmd}")

    rig.rpc("rtc-save")
    print("  nvram persisted")
    mark(state_dir, "phase-a")


def label(rig: Rig, state_dir: str) -> None:
    """Label the blank boot disk from the install CD via fx.ARCS.

    fx is driven through its menus: `label`/`create`/`all` builds a standard
    SGI volume header with the default partitions, `sync` writes it, and
    `exit` returns to the PROM's maintenance menu (`Option?`).
    """
    rig.rpc("start")
    enter_prom_monitor(rig)
    print("  booting fx.ARCS from the install CD")
    rig.send(FX_BOOT)

    # fx asks for the target device before its main menu; the three defaults
    # are dksc, controller 0, drive 1. Menu prompts carry their default in
    # parentheses; unrelated "fx: Warning" lines do not, so they won't match.
    # The console emits both \r\n and \n\r, so a line start may carry a CR.
    prompt_re = r"(?m)(fx> |^[\r]?fx: [^\n]* = \([^\n]*\)\s*$)"
    while True:
        matched, m = rig.expect_re(prompt_re, 300)
        if m.group(0) == "fx> ":
            break
        rig.send("")

    steps = [
        ("l", r"fx/label> "),
        ("c", r"fx/label/create> "),
        ("a", r"fx/label/create> "),
        ("..", r"fx/label> "),
        ("sync", r"fx/label> "),
        ("..", r"fx> "),
        ("exit", r"(Option\?|>> )"),
    ]
    for cmd, pattern in steps:
        print(f"  fx> {cmd}")
        rig.send(cmd)
        rig.expect_re(pattern, 300)

    mark(state_dir, "label")


def to_inst_prompt(rig: Rig, timeout: float = 1200.0) -> None:
    """Walk from the PROM maintenance menu into miniroot's first Inst> prompt.

    The PROM asks for the installation medium (default: local CD-ROM) and to
    confirm the disc; miniroot then offers to make the root filesystem and asks
    for the block size. Matching goes by earliest position in the buffer so a
    prompt is never skipped because a later one matched first.
    """
    rules = [
        (re.compile(r"Option\?"), "2"),
        (re.compile(r"Make new file system"), "yes"),
        (re.compile(r"Block size"), "4096"),
        (re.compile(r"\[y/n\]"), "y"),
        (re.compile(r"(?i)press\s*<enter>"), ""),
        (re.compile(r"(?i)<enter>\s*to\s*start"), ""),
        (re.compile(r"(?i)insert[^\n]*(?:cd|disc)"), ""),
        (re.compile(r"ERROR:"), "!"),
        (re.compile(r"Inst>"), None),
    ]
    deadline = time.monotonic() + timeout
    while True:
        best = None
        for rx, response in rules:
            m = rx.search(rig.buf)
            if m and (best is None or m.start() < best[0].start()):
                best = (m, response)
        if best is not None:
            m, response = best
            context, rig.buf = rig.buf[:m.end()], rig.buf[m.end():]
            if response is None:
                return
            if response == "!":
                raise RigError(f"miniroot reported an error:\n{context}")
            rig.send(response)
            continue
        if time.monotonic() >= deadline:
            tail = rig.buf[-3000:]
            rig.buf = ""
            raise RigError(f"miniroot did not reach Inst>; last output:\n{tail}")
        rig.pump()
        time.sleep(POLL_SECS)


def scan_path(rig: Rig, path: str, timeout: float = 1800.0) -> bool:
    """Point inst's `from` loop at one distribution path; True if it scanned.

    A scan can raise the maintenance/feature stream choice part-way through
    (after the README scrolls), so the maintenance stream (6.5.7m, the rebuild
    target) is selected there; a switch-distributions confirmation before any
    selections is safe to confirm. An absent path is a miss, not an error.
    """
    rig.clear()
    rig.send(path)
    deadline = time.monotonic() + timeout
    while True:
        candidates = []
        for rx, response in (
            (re.compile(r"100% Done\."), None),
            (
                re.compile(
                    r"(?i)no such file|does not exist|no products|cannot open|not a distribution"
                ),
                "ERR",
            ),
            (re.compile(r"Do you really want to switch distributions"), "y"),
            (re.compile(r"Please enter a choice"), "choice"),
        ):
            m = rx.search(rig.buf)
            if m:
                candidates.append((m, response))
        if candidates:
            m, response = min(candidates, key=lambda c: c[0].start())
            context, rig.buf = rig.buf[:m.end()], rig.buf[m.end():]
            if response is None:
                return True
            if response == "ERR":
                return False
            if response == "y":
                rig.send("y")
            else:
                rig.send("1" if "maintenance stream" in context else "")
            continue
        if time.monotonic() >= deadline:
            tail = rig.buf[-3000:]
            rig.buf = ""
            raise RigError(f"scanning {path} did not finish; last output:\n{tail}")
        rig.pump()
        time.sleep(POLL_SECS)


def from_prompt(rig: Rig) -> None:
    """Advance inst's `from` command to its path prompt.

    `from` offers the maintenance/feature stream choice before it asks for a
    distribution path. The rebuild target is IRIX 6.5.7m, the maintenance
    stream, so pick option 1 when that menu is on screen; any other numbered
    prompt gets its default. The prompt is matched on its literal text so the
    word "stream" inside the explanatory prose cannot fire early.
    """
    while True:
        out, pattern = rig.expect(["Install software from:", "Please enter a choice"], 300)
        if pattern == "Install software from:":
            return
        if "maintenance stream" in out:
            print("  selecting maintenance stream")
            rig.send("1")
        else:
            print("  accepting default choice")
            rig.send("")


def load_distributions(rig: Rig, disc_names: list[str]) -> None:
    """Scan every install disc through inst's `from` loop.

    The changer is cycled with `cdrom-eject` (disc 0 is already mounted when
    the loop starts). Foundation discs put their products in /CDROM/dist; the
    overlay discs historically also carry an unbundled tree, so both paths are
    offered for every disc and a missing one is skipped.
    """
    rig.send("from")
    from_prompt(rig)

    for index, name in enumerate(disc_names):
        if index > 0:
            disc = rig.rpc("cdrom-eject", id=4)
            print(f"  cdrom-eject 4 -> {disc}")
        for path in ("/CDROM/dist", "/CDROM/dist/unbundled"):
            ok = scan_path(rig, path)
            print(f"  {name}: {path} {'scanned' if ok else 'not present'}")

    rig.send("done")
    rig.expect(["Inst>"], 60)


def drive_install(rig: Rig, timeout: float = 4 * 3600.0) -> None:
    """Run `go` to completion, answering CD-swap prompts by cycling the changer.

    inst asks for each disc by name (`Please insert the "X" CD.`). The SCSI
    changer on ID 4 is cycled until the mounted image's filename contains the
    requested name, then Enter lets the install resume.
    """
    requested: str | None = None
    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= deadline:
            raise RigError("install did not finish within the time budget")
        rig.pump()

        m = re.search(r'[Ii]nsert the ["\']([^"\']+)["\'] CD', rig.buf)
        if m:
            requested = m.group(1)
            rig.buf = ""
            print(f"  inst asks for CD: {requested}")
            for _ in range(7):
                data = rig.rpc("cdrom-eject", id=4)
                print(f"    mounted {data.get('new_disc', data)}")
                if disc_matches(requested, str(data.get("new_disc", ""))):
                    break
            rig.send("")
            continue

        m = re.search(r"Insert .*CD-ROM.*press", rig.buf)
        if m:
            rig.buf = ""
            rig.send("")
            continue

        if "Installations and removals were successful" in rig.buf:
            print("  Installations and removals were successful.")
            return

        if "Conflicts must be resolved" in rig.buf:
            raise RigError("inst reported unresolved conflicts (rulesoverride should prevent this)")

        if "ERROR:" in rig.buf:
            tail = rig.buf[-2000:]
            raise RigError(f"inst reported an error:\n{tail}")

        time.sleep(POLL_SECS)


def install(rig: Rig, media_names: list[str], state_dir: str) -> None:
    rig.rpc("start")
    # The previous phase left the PROM waiting at a prompt the driver already
    # consumed; a bare newline redraws it (safe at both the PROM menu and Inst>).
    rig.send("")
    to_inst_prompt(rig)
    for cmd in ("set page_output off", "set rulesoverride on"):
        rig.send(cmd)
        rig.expect(["Inst>"], 60)
    print("  inst ready; page_output off, rulesoverride on")

    load_distributions(rig, media_names)

    for cmd in (
        "keep *",
        "install standard",
        "install prereqs",
        "keep incompleteoverlays",
    ):
        rig.send(cmd)
        rig.expect(["Inst>"], 180)
        print(f"  {cmd}")

    print("  go (this takes one to two emulated hours)")
    rig.send("go")
    drive_install(rig)

    # Post-install: requickstart walks the whole tree, then autoconfig runs,
    # then inst finally offers the restart.
    rig.send("quit")
    rig.expect(["Restart"], 3600)
    rig.send("y")
    mark(state_dir, "install")


def verify(rig: Rig, state_dir: str, ic: str, evidence: str) -> None:
    """Log in on the console and record the acceptance evidence.

    The installed volume header still has the miniroot `ide` blob, so sash can
    stop on its "miniroot install failed" repair prompt before init; answer
    `c` (continue without state fixup) and remove the blob once logged in.
    """
    rig.rpc("start")
    while True:
        out, pattern = rig.expect(
            ["console login:", "login:", "Enter 'c' to continue"], 1800
        )
        if pattern == "Enter 'c' to continue":
            rig.send("c")
            continue
        break

    rig.run_ic(ic, "login")
    rig.run_ic(
        ic,
        "run",
        "--timeout",
        "120",
        "dvhtool -v delete ide /dev/rdsk/dks0d1vh",
        capture_output=True,
        text=True,
    )

    lines = ["# Rig verification " + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n"]
    # `versions eoe` is the version proof: it names the release as 6.5.7m,
    # the maintenance-stream target, which uname alone does not. Pipe it
    # through cat so IRIX does not page the output on the console.
    for cmd in ("uname -a", "hinv", "versions eoe | cat"):
        proc = rig.run_ic(ic, "run", "--timeout", "120", cmd, capture_output=True, text=True)
        print(proc.stdout)
        lines.append(f"$ {cmd}\n{proc.stdout}\n")
    with open(evidence, "w") as f:
        f.writelines(lines)
    mark(state_dir, "verify")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=["phase-a", "label", "install", "verify"])
    # There is deliberately no socket default: guessing /tmp/iris.sock would
    # drive the sibling session's rig, which ADR-0004 forbids.
    parser.add_argument("--socket", default=os.environ.get("IRIS_SOCKET"))
    parser.add_argument("--log", default=os.environ.get("RIG_DRIVER_LOG"))
    parser.add_argument("--state-dir", default=os.environ.get("RIG_STATE_DIR", ".rig-state"))
    parser.add_argument("--evidence", default=os.environ.get("RIG_EVIDENCE", "rig-evidence.txt"))
    parser.add_argument("--ic", default=os.environ.get("RIG_IRIS_CI"))
    parser.add_argument(
        "--disc",
        action="append",
        default=None,
        help="install disc filename in changer order (repeatable; default = the four 6.5.7 media)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not args.socket:
        parser.error("--socket or $IRIS_SOCKET is required (the rig's socket, never /tmp/iris.sock)")
    if args.phase == "verify" and not args.ic:
        parser.error("verify needs --ic or $RIG_IRIS_CI (the rig's iris-ci binary)")

    media_names = args.disc or [
        "IRIX 6.5.7 Installation Tools and Overlays (1 of 2).iso",
        "IRIX 6.5 Foundation 1.iso",
        "IRIX 6.5 Foundation 2.iso",
        "IRIX 6.5.7 Overlays (2 of 2).iso",
    ]

    rig = Rig(args.socket, args.log, echo=not args.quiet)
    try:
        if marked(args.state_dir, args.phase):
            print(f"{args.phase}: already done ({args.state_dir})")
            return 0
        if args.phase == "phase-a":
            phase_a(rig, args.state_dir)
        elif args.phase == "label":
            label(rig, args.state_dir)
        elif args.phase == "install":
            install(rig, media_names, args.state_dir)
        elif args.phase == "verify":
            verify(rig, args.state_dir, args.ic, args.evidence)
    except RigError as e:
        print(f"{args.phase}: FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        rig.close()
    print(f"{args.phase}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
