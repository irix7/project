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
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

POLL_SECS = 0.5
DEFAULT_TIMEOUT = 120.0


def _load_rig_state():
    """Load the shared generation-bound marker module (rig-state.py).

    The dashed filename cannot be imported by name, so it is loaded from its
    path, like oracle-driver does with this file.
    """
    path = Path(__file__).with_name("rig-state.py")
    spec = importlib.util.spec_from_file_location("rig_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rig_state = _load_rig_state()

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


SUCCESS = "Installations and removals were successful"
ERROR_MARKERS = ("ERROR:", "Installations and removals failed")
CONFLICT_MARKERS = ("Conflicts must be resolved", "Resolve conflicts by typing")

# Lines that may match an error marker yet are known harmless. Deliberately
# empty: the audit's stored transcript paired an incompatible-subsystems error
# with "install: ok", and that error's harmlessness was never established, so
# a mixed transcript must fail. An entry here needs a cited transcript and a
# test proving the exact line is harmless; blanket suppression is forbidden.
HARMLESS_LINES: tuple[str, ...] = ()

# The maintenance stream is the rebuild target (6.5.7m); a "versions eoe"
# output that does not name it cannot certify the guest.
RELEASE_TARGET = "6.5.7m"


def _without_harmless(text: str, harmless: tuple[str, ...] = HARMLESS_LINES) -> str:
    if not harmless:
        return text
    return "\n".join(
        line for line in text.splitlines() if not any(ok in line for ok in harmless)
    )


def classify_install_transcript(
    text: str, harmless: tuple[str, ...] = HARMLESS_LINES
) -> str | None:
    """Classify a whole inst transcript: error, conflict, success or None.

    Errors and conflicts take precedence over the success line: a run that
    printed both is not a success, which is precisely the mixed transcript the
    audit found certifying a bad install. Only lines explicitly allowlisted as
    harmless are dropped before the markers are looked for.
    """
    cleaned = _without_harmless(text, harmless)
    if any(marker in cleaned for marker in ERROR_MARKERS):
        return "error"
    if any(marker in cleaned for marker in CONFLICT_MARKERS):
        return "conflict"
    if SUCCESS in cleaned:
        return "success"
    return None


def has_failure(text: str, harmless: tuple[str, ...] = HARMLESS_LINES) -> bool:
    """True when the text carries an error or conflict marker."""
    outcome = classify_install_transcript(text, harmless)
    return outcome in ("error", "conflict")


def maintenance_stream_verified(versions_output: str) -> bool:
    """True when `versions eoe | cat` names the 6.5.7m maintenance stream."""
    return (
        re.search(rf"(?<![0-9A-Za-z.]){re.escape(RELEASE_TARGET)}(?![0-9A-Za-z])", versions_output)
        is not None
    )


def stream_choice_number(menu_text: str) -> str | None:
    """The option number whose menu line names the maintenance stream.

    inst numbers the stream menu; the option is parsed from the text rather
    than assumed, so a renumbered or reordered menu cannot silently select the
    feature stream. None means the maintenance stream is not on the menu.
    """
    for line in menu_text.splitlines():
        if "maintenance stream" in line.lower():
            match = re.match(r"\s*([0-9]+)\s*[.)]", line)
            if match:
                return match.group(1)
    return None


def is_stream_menu(menu_text: str) -> bool:
    """True when the choice text is inst's stream menu (not a generic prompt)."""
    return "stream" in menu_text.lower()


class StreamSelection:
    """Answer inst's maintenance/feature stream menu, verifying the outcome.

    The menu is answered by parsing the maintenance option's number; a menu
    that mentions streams but offers no maintenance option is an error, and a
    choice that is re-offered without the scan making progress fails after a
    bounded number of consecutive attempts instead of looping. `progress`
    resets the counter, because each disc in the changer legitimately offers
    its own menu. No other prompt is answered blindly here.
    """

    def __init__(self, max_consecutive: int = 3):
        self.max_consecutive = max_consecutive
        self.attempts = 0
        self.selected = False

    def answer(self, menu_text: str) -> str:
        number = stream_choice_number(menu_text)
        if number is None:
            raise RigError(
                "inst offered a stream menu with no maintenance stream option"
            )
        self.attempts += 1
        if self.attempts > self.max_consecutive:
            raise RigError("inst rejected the maintenance stream choice repeatedly")
        self.selected = True
        return number

    def progress(self) -> None:
        """A completed scan; the next menu is a fresh selection."""
        self.attempts = 0


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

    def expect(self, patterns, timeout: float = DEFAULT_TIMEOUT, earliest: bool = False):
        """Consume the first matching pattern; with `earliest`, the one that
        appears first in the buffer rather than the first in `patterns`.

        Prompt detectors list patterns most-specific first, but a stale prompt
        earlier in the buffer (a login line followed by a shell prompt after a
        retry) must win over a later-but-earlier-listed one. `earliest` gives
        that position-based choice where a state machine depends on it.
        """
        if isinstance(patterns, str):
            patterns = [patterns]
        deadline = time.monotonic() + timeout
        while True:
            idx = -1
            match = None
            for pattern in patterns:
                found = self.buf.find(pattern)
                if found < 0:
                    continue
                if not earliest:
                    idx, match = found, pattern
                    break
                if match is None or found < idx:
                    idx, match = found, pattern
            if match is not None:
                end = idx + len(match)
                matched, self.buf = self.buf[:end], self.buf[end:]
                return matched, match
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
    rig_state.mark(
        state_dir,
        "phase-a",
        "NVRAM persisted SystemPartition, OSLoadPartition and console=d",
    )


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

    rig_state.mark(
        state_dir,
        "label",
        "fx.ARCS labelled and synced the boot disk's volume header",
    )


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


# Every install disc must offer at least one distribution tree. /CDROM/dist is
# the modern layout; the overlay discs historically carry /CDROM/dist/unbundled
# instead, so it is accepted as the alternative rather than as a second miss.
REQUIRED_DISC_PATHS: tuple[str, ...] = ("/CDROM/dist",)
ALTERNATE_DISC_PATHS: tuple[str, ...] = ("/CDROM/dist/unbundled",)


def disc_scan_error(disc: str, results: list[tuple[str, bool]]) -> str | None:
    """The failure reason when a disc scanned no distribution tree at all.

    A miss on /CDROM/dist is tolerated only when the alternate path scanned,
    so a required disc that presents nothing fails the phase instead of being
    printed "not present" and skipped.
    """
    if any(ok for _, ok in results):
        return None
    tried = ", ".join(path for path, _ in results)
    return f"{disc}: no installable distribution found (tried {tried})"


def scan_path(
    rig: Rig,
    selection: StreamSelection,
    path: str,
    timeout: float = 1800.0,
) -> bool:
    """Point inst's `from` loop at one distribution path; True if it scanned.

    A scan can raise the maintenance/feature stream choice part-way through
    (after the README scrolls); the maintenance stream (6.5.7m, the rebuild
    target) is selected from that menu by parsing its option number, and a
    stream menu without a maintenance option is an error. A
    switch-distributions confirmation before any selections is safe to
    confirm. An absent path is a miss, not an error.
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
                selection.progress()
                return True
            if response == "ERR":
                return False
            if response == "y":
                rig.send("y")
            elif is_stream_menu(context):
                number = selection.answer(context)
                print(f"  selecting maintenance stream ({number})")
                rig.send(number)
            else:
                # Not the stream menu: answer the prompt's default, as before.
                rig.send("")
            continue
        if time.monotonic() >= deadline:
            tail = rig.buf[-3000:]
            rig.buf = ""
            raise RigError(f"scanning {path} did not finish; last output:\n{tail}")
        rig.pump()
        time.sleep(POLL_SECS)


def from_prompt(rig: Rig, selection: StreamSelection) -> None:
    """Advance inst's `from` command to its path prompt.

    `from` offers the maintenance/feature stream choice before it asks for a
    distribution path. The rebuild target is IRIX 6.5.7m, the maintenance
    stream, so its parsed option number is selected; a stream menu without a
    maintenance option fails rather than defaulting. Any other numbered prompt
    gets its default. The prompt is matched on its literal text so the word
    "stream" inside the explanatory prose cannot fire early.
    """
    while True:
        out, pattern = rig.expect(
            ["Install software from:", "Please enter a choice"], 300, earliest=True
        )
        if pattern == "Install software from:":
            return
        if is_stream_menu(out):
            number = selection.answer(out)
            print(f"  selecting maintenance stream ({number})")
            rig.send(number)
        else:
            print("  accepting default choice")
            rig.send("")


def load_distributions(rig: Rig, disc_names: list[str]) -> None:
    """Scan every install disc through inst's `from` loop.

    The changer is cycled with `cdrom-eject` (disc 0 is already mounted when
    the loop starts). Every disc must present at least one distribution tree:
    /CDROM/dist, or the overlays' historical /CDROM/dist/unbundled. A disc
    that presents neither raises rather than being skipped.
    """
    selection = StreamSelection()
    rig.send("from")
    from_prompt(rig, selection)

    for index, name in enumerate(disc_names):
        if index > 0:
            disc = rig.rpc("cdrom-eject", id=4)
            print(f"  cdrom-eject 4 -> {disc}")
        results: list[tuple[str, bool]] = []
        for path in (*REQUIRED_DISC_PATHS, *ALTERNATE_DISC_PATHS):
            ok = scan_path(rig, selection, path)
            results.append((path, ok))
            print(f"  {name}: {path} {'scanned' if ok else 'not present'}")
        error = disc_scan_error(name, results)
        if error:
            raise RigError(error)

    rig.send("done")
    rig.expect(["Inst>"], 60)


def answer_cd_request(rig: Rig, requested: str) -> None:
    """Cycle the changer until the disc inst asked for is mounted.

    inst asks for each disc by name (`Please insert the "X" CD.`). The SCSI
    changer on ID 4 is cycled until the mounted image's filename matches the
    request; a changer that never produces the disc fails loudly instead of
    sending Enter into a prompt that can only fail.
    """
    for _ in range(7):
        data = rig.rpc("cdrom-eject", id=4)
        print(f"    mounted {data.get('new_disc', data)}")
        if disc_matches(requested, str(data.get("new_disc", ""))):
            return
    raise RigError(f"changer never mounted the requested CD: {requested!r}")


def drive_install(rig: Rig, timeout: float = 4 * 3600.0) -> str:
    """Run `go` to completion, answering CD-swap prompts by cycling the changer.

    The transcript is accumulated separately from the prompt buffer, because
    CD-swap handling consumes the buffer and a success line must never hide an
    earlier error. Classification happens once, over the whole run, with
    errors and conflicts taking precedence over success; the transcript is
    returned so callers can keep it as evidence.
    """
    transcript: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= deadline:
            raise RigError("install did not finish within the time budget")
        rig.pump()
        if rig.buf:
            transcript.append(rig.buf)

        if SUCCESS in rig.buf or has_failure(rig.buf):
            break

        m = re.search(r'[Ii]nsert the ["\']([^"\']+)["\'] CD', rig.buf)
        if m:
            requested = m.group(1)
            rig.buf = ""
            print(f"  inst asks for CD: {requested}")
            answer_cd_request(rig, requested)
            rig.send("")
            continue

        m = re.search(r"Insert .*CD-ROM.*press", rig.buf)
        if m:
            rig.buf = ""
            rig.send("")
            continue

        time.sleep(POLL_SECS)

    text = "".join(transcript)
    outcome = classify_install_transcript(text)
    if outcome == "error":
        raise RigError(f"inst reported an error in the go transcript:\n{text[-2000:]}")
    if outcome == "conflict":
        raise RigError(
            "inst reported unresolved conflicts (rulesoverride should prevent this):\n"
            f"{text[-2000:]}"
        )
    if outcome != "success":
        raise RigError(f"inst `go` ended without the success line:\n{text[-2000:]}")
    print("  Installations and removals were successful.")
    return text


def quit_and_restart(rig: Rig, timeout: float = 3600.0) -> None:
    """Quit inst and answer its restart prompt.

    The restart prompt must actually arrive: a timeout raises from expect_re,
    the install phase is marked failed, and the marker is never written. The
    caller only marks after this returns.
    """
    rig.send("quit")
    rig.expect_re(r"(?i)restart", timeout)
    rig.send("y")


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
    # then inst finally offers the restart. `quit` must reach that prompt; a
    # timeout here is a failed handshake, not a success, and is never marked.
    quit_and_restart(rig)
    rig_state.mark(
        state_dir,
        "install",
        "inst go reported success and the quit/restart handshake was answered",
    )


def resume_to_login(rig: Rig, ic: str, timeout: float = 1800.0) -> None:
    """Get from login, a logged-in shell or the PROM to a logged-in shell.

    An interrupted verify resumes from any of those console states rather than
    needing its marker dropped by hand. The earliest-position match keeps a
    stale login line from winning over the shell prompt that followed it. The
    `Enter 'c' to continue` sash repair prompt (the installed volume header
    still carries the miniroot `ide` blob) is answered and the wait continues.
    """
    while True:
        out, pattern = rig.expect(
            [
                "console login:",
                "login:",
                "# ",
                "Option?",
                ">> ",
                "Enter 'c' to continue",
            ],
            timeout,
            earliest=True,
        )
        if pattern == "Enter 'c' to continue":
            rig.send("c")
            continue
        if pattern == "# ":
            return
        if pattern in ("console login:", "login:"):
            # iris-ci's login waits server-side until the shell answers, the
            # same contract ensure_shell relies on; no extra prompt read here.
            rig.run_ic(ic, "login")
        else:
            # PROM: let iris-ci boot through to the login prompt, then log in.
            rig.run_ic(ic, "boot")
            rig.run_ic(ic, "login")
        return


def verify(rig: Rig, state_dir: str, ic: str, evidence: str) -> None:
    """Log in on the console and record the acceptance evidence.

    Resumes from login, shell or PROM (`resume_to_login`), removes the stale
    miniroot `ide` blob, and only records the evidence after `versions eoe`
    has been checked to name the 6.5.7m maintenance stream. A wrong or
    unrecognised release raises: the marker must certify the target release,
    not merely a successful login.
    """
    rig.rpc("start")
    resume_to_login(rig, ic)

    # Removing the stale file is cleanup, not a postcondition: a resumed
    # verify whose first attempt already deleted the blob would otherwise die
    # here. The blob's only effect is the sash repair prompt, which
    # resume_to_login answers, so a tolerated failure is safe.
    rig.run_ic(
        ic,
        "run",
        "--timeout",
        "120",
        "dvhtool -v delete ide /dev/rdsk/dks0d1vh >/dev/null 2>&1 || true",
        capture_output=True,
        text=True,
    )

    # `versions eoe` is the version proof: it names the release as 6.5.7m,
    # the maintenance-stream target, which uname alone does not. Pipe it
    # through cat so IRIX does not page the output on the console.
    outputs: dict[str, str] = {}
    for cmd in ("uname -a", "hinv", "versions eoe | cat"):
        proc = rig.run_ic(
            ic, "run", "--timeout", "120", cmd, capture_output=True, text=True
        )
        print(proc.stdout)
        outputs[cmd] = proc.stdout

    if not maintenance_stream_verified(outputs["versions eoe | cat"]):
        raise RigError(
            "versions eoe did not name 6.5.7m (maintenance stream); "
            "refusing to record verification"
        )

    lines = ["# Rig verification " + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n"]
    for cmd in ("uname -a", "hinv", "versions eoe | cat"):
        lines.append(f"$ {cmd}\n{outputs[cmd]}\n")
    with open(evidence, "w") as f:
        f.writelines(lines)
    rig_state.mark(
        state_dir,
        "verify",
        "logged in; versions eoe named 6.5.7m; evidence recorded",
    )


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
        if rig_state.marked(args.state_dir, args.phase):
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
