#!/usr/bin/env python3
"""Drive the MIPSpro oracle install over the rig's CI socket (issue #3).

Where install-driver.py (issue #2) provisions the bare 6.5.7m guest, this
driver installs the SGI toolchain that makes the guest an oracle: the 7.3
compiler from the MIPSpro All-Compiler CD, the headers/startfiles from the
Development Libraries disc and the 7.4 runtime from the CEE disc. It speaks
the same plain JSON protocol as install-driver.py and reuses its Rig class,
because inst is prompt-driven and iris-ci's one-shot `run` cannot answer its
menus.

The media are loaded and mounted by scripts/rig/oracle.sh before each `inst`
invocation; this driver only drives the interactive session:

  ensure-shell   get from wherever the guest is to a logged-in shell
  inst PRODUCTS  scan the mounted /CDROM/dist, keep everything, install the
                 named products, `go`, then quit through requickstart
  sets           print slug<TAB>media<TAB>products for oracle.sh (one per line)

`sets` is the single source of truth for what gets installed and in which
order; the unit tests assert it covers headers, startfiles, libc/libm, the
7.3 compiler and the 7.4 runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

INSTALL_DRIVER = Path(__file__).with_name("install-driver.py")
_spec = importlib.util.spec_from_file_location("install_driver", INSTALL_DRIVER)
install_driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_driver)

Rig = install_driver.Rig
RigError = install_driver.RigError


class OracleSet(NamedTuple):
    slug: str
    media: str
    products: str


# The oracle install, in order. The Development Libraries disc carries the
# headers (irix_dev.sw.headers) and the crt startfiles/dev libs (dev.sw.lib);
# the All-Compiler CD the MIPSpro 7.3 driver, phases and C front end; the CEE
# disc the 7.4 runtime libs. `keep *` before each install keeps it surgical.
# Oracle.sh and status.sh read this list through the `sets` action, so the
# media and products are spelled only here.
ORACLE_SETS = [
    OracleSet("libs", "IRIX 6.5 Development Libraries June 1998.iso",
              "dev.sw.lib irix_dev.sw.headers"),
    OracleSet("mipspro", "MIPSpro All-Compiler CD May 1999.iso",
              "compiler_dev c_dev c_fe c_fe.sw64.lib compiler_dev.sw64.lib"),
    OracleSet("cee", "Compiler Execution Environment 7.4.iso",
              "compiler_eoe compiler_eoe.sw64.lib compiler_eoe.sw64.unix"),
]

SUCCESS = "Installations and removals were successful"


def classify_go(text: str) -> str | None:
    """Classify the tail of an inst `go` transcript.

    Matches the exact strings inst 3.8 emits on the install media: success,
    an unresolved conflict (defensive — `keep *` plus rulesoverride should
    prevent it) or an INSTALL/exitop error.
    """
    if SUCCESS in text:
        return "success"
    if "Resolve conflicts by typing" in text or "Conflicts must be resolved" in text:
        return "conflict"
    if "ERROR:" in text or "Installations and removals failed" in text:
        return "error"
    return None


def ensure_shell(rig: Rig, ic: str) -> None:
    """Get from wherever the guest is to a logged-in shell prompt.

    The rig starts paused, so `start` first. Then the guest is either already
    at a shell (`# `), at a login prompt (log in over iris-ci), or at the PROM
    maintenance menu (boot it). Waiting happens on serial because `iris-ci run`
    would blind-fire a command at a login prompt as a username.
    """
    rig.rpc("start")
    # A bare newline redraws whatever prompt the guest is sitting at, so the
    # prompt does not have to be captured from earlier output. The wait budget
    # covers a cold boot; only a dead guest reaches it.
    rig.send("")
    out, pattern = rig.expect(
        ["console login:", "login:", "# ", "Option?", ">> "], 900
    )
    if pattern == "# ":
        return
    if pattern in ("console login:", "login:"):
        subprocess.run([ic, "login"], check=True)
        return
    # PROM: let iris-ci boot through to the login prompt, then log in.
    subprocess.run([ic, "boot"], check=True)
    subprocess.run([ic, "login"], check=True)


def run_inst(rig: Rig, products: str) -> None:
    """Scan the mounted distribution, install `products`, go, quit."""
    rig.send("")
    rig.send("inst")
    rig.expect(["Inst> "], 300)
    print(f"  inst ready; installing: {products}")
    for cmd in ("set page_output off", "set rulesoverride on"):
        rig.send(cmd)
        rig.expect(["Inst> "], 60)
    print("  scanning /CDROM/dist")
    rig.send("from /CDROM/dist")
    rig.expect(["Inst> "], 1800)
    rig.send("keep *")
    rig.expect(["Inst> "], 60)
    rig.send(f"install {products}")
    rig.expect(["Inst> "], 300)

    print("  go")
    rig.send("go")
    outcome = None
    deadline = time.monotonic() + 4 * 3600.0
    while outcome is None:
        outcome = classify_go(rig.buf)
        if outcome is None:
            if time.monotonic() >= deadline:
                tail, rig.buf = rig.buf[-3000:], ""
                raise RigError(f"inst `go` did not finish; last output:\n{tail}")
            rig.pump()
            time.sleep(0.5)
    if outcome == "conflict":
        tail, rig.buf = rig.buf[-3000:], ""
        raise RigError(f"inst reported unresolved conflicts:\n{tail}")
    if outcome == "error":
        tail, rig.buf = rig.buf[-3000:], ""
        raise RigError(f"inst reported an error:\n{tail}")
    rig.expect(["Inst> "], 120)
    print("  successful; quit (requickstart may take a while)")
    rig.send("quit")
    rig.expect_re(r"(?m)^IRIS \d+# $", 3600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=["ensure-shell", "inst", "sets"])
    parser.add_argument("products", nargs="?", help="for `inst`: the products to install")
    parser.add_argument("--socket", default=os.environ.get("IRIS_SOCKET"))
    parser.add_argument("--log", default=os.environ.get("RIG_DRIVER_LOG"))
    parser.add_argument("--ic", default=os.environ.get("RIG_IRIS_CI"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.action == "sets":
        for slug, media, products in ORACLE_SETS:
            print(f"{slug}\t{media}\t{products}")
        return 0

    if not args.socket:
        parser.error("--socket or $IRIS_SOCKET is required (the rig's socket, never /tmp/iris.sock)")
    if args.action == "inst" and not args.products:
        parser.error("inst needs the products to install")

    rig = Rig(args.socket, args.log, echo=not args.quiet)
    try:
        if args.action == "ensure-shell":
            if not args.ic:
                parser.error("ensure-shell needs --ic or $RIG_IRIS_CI")
            ensure_shell(rig, args.ic)
        elif args.action == "inst":
            run_inst(rig, args.products)
    except RigError as e:
        print(f"{args.action}: FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        rig.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
