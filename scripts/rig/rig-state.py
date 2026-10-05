#!/usr/bin/env python3
"""Generation-bound, atomic state markers for the rig (issue #25).

A phase marker may only certify the guest it was actually produced from. A
timestamp alone says nothing about which boot disk or rig configuration was
tested, so a replacement disk or an edited ``iris.toml`` could be trusted on
the strength of an old marker. This module derives a *generation* from the two
inputs that define a guest's identity and binds every marker to it:

  * the boot disk: its size, device, inode and a bounded digest (64 KiB) of
    the volume header at the start of ``$RIG_DISK``. File timestamps are
    deliberately excluded: the emulator writes inside the partitions during
    an install, and that must not invalidate the phase markers of the install
    in progress. A replacement disk differs in inode and/or header digest; a
    removed disk is the distinct ``absent`` identity.
  * the rig config file ``$RIG_CONFIG``: a digest of its bytes, so any edit
    invalidates dependent markers.

Markers keep their historical on-disk names (``<name>.done``) and are written
atomically (a same-directory temp file followed by ``os.replace``), so a
reader never observes a partial file and a crash leaves the previous marker
intact. The format is documented in ``docs/rig.md``.

The module is importable, and doubles as the CLI the shell scripts use:

    rig-state.py generation
    rig-state.py mark NAME [--postcondition TEXT]
    rig-state.py marked NAME        # exit 0 only for the current generation
    rig-state.py status NAME        # human-readable state
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import time

DISK_ENV = "RIG_DISK"
CONFIG_ENV = "RIG_CONFIG"
STATE_ENV = "RIG_STATE_DIR"
DEFAULT_STATE_DIR = ".rig-state"

MARKER_SUFFIX = ".done"
# Bounded by design: the volume header and partition table live at the start of
# the image, so a 20GB sparse disk costs one 64 KiB read, never a full hash.
VOLUME_HEADER_BYTES = 64 * 1024


def _short(generation: str) -> str:
    return generation[:12] if generation else "-"


def _disk_identity(disk: str | None) -> str:
    """A stable identity for the boot disk, or ``absent``/``unreadable``.

    ``st_mtime_ns`` is not part of it on purpose: every emulator write to any
    partition updates it, and a marker must survive its own phase's writes.
    """
    if not disk:
        return "absent"
    try:
        st = os.stat(disk)
    except FileNotFoundError:
        return "absent"
    except OSError as e:
        return f"unreadable:{e.errno}"
    try:
        with open(disk, "rb") as f:
            header = f.read(VOLUME_HEADER_BYTES)
    except OSError as e:
        return f"unreadable:{e.errno}"
    digest = hashlib.sha256(header).hexdigest()
    return f"size={st.st_size},dev={st.st_dev},ino={st.st_ino},vh={digest}"


def _config_identity(config: str | None) -> str:
    if not config:
        return "absent"
    try:
        with open(config, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return "absent"
    except OSError as e:
        return f"unreadable:{e.errno}"
    return "sha256=" + hashlib.sha256(data).hexdigest()


def generation(
    state_dir: str | None = None,
    *,
    disk: str | None = None,
    config: str | None = None,
) -> str:
    """The identity a marker certifies.

    ``state_dir`` is accepted for symmetry with :func:`mark` and friends; the
    identity itself comes from the disk and config only. Explicit ``disk`` and
    ``config`` override the environment, which is what the tests use.
    """
    del state_dir  # the marker directory is not part of the guest identity
    if disk is None:
        disk = os.environ.get(DISK_ENV)
    if config is None:
        config = os.environ.get(CONFIG_ENV)
    material = f"disk:{_disk_identity(disk)}\nconfig:{_config_identity(config)}\n"
    return hashlib.sha256(material.encode()).hexdigest()


def marker_path(state_dir: str, name: str) -> str:
    return os.path.join(state_dir, name + MARKER_SUFFIX)


def _atomic_write(path: str, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file and ``os.replace``.

    The temp file lives in the same directory, so the rename is atomic. On any
    failure the temp file is removed and the previous file is untouched.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def mark(
    state_dir: str,
    name: str,
    postcondition: str = "unspecified",
) -> str:
    """Atomically record NAME as done for the current generation.

    ``postcondition`` is the human-readable claim the marker makes; callers
    must only reach this call after verifying it. Returns the generation
    written, so a caller can report it.
    """
    gen = generation(state_dir)
    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    text = (
        f"generation: {gen}\n"
        f"created: {created}\n"
        f"postcondition: {postcondition}\n"
    )
    _atomic_write(marker_path(state_dir, name), text)
    return gen


def read_marker(state_dir: str, name: str) -> dict[str, str] | None:
    """Parse a marker, or ``None`` when it does not exist.

    A marker whose generation line is absent (the historical timestamp-only
    format) is returned without ``generation``; callers treat that as stale.
    """
    path = marker_path(state_dir, name)
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    fields: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def marked(state_dir: str, name: str) -> bool:
    """True only when NAME's marker exists and certifies this generation."""
    gen = generation(state_dir)
    marker = read_marker(state_dir, name)
    if not marker:
        return False
    return marker.get("generation") == gen


def status(state_dir: str, name: str) -> str:
    """A human-readable state for NAME (never raises)."""
    marker = read_marker(state_dir, name)
    if marker is None:
        return "pending"
    saved = marker.get("generation", "")
    if not saved:
        created = marker.get("created", "unknown time")
        return f"legacy timestamp-only marker ({created})"
    current = generation(state_dir)
    if saved != current:
        return (
            "stale generation "
            f"(marker {_short(saved)}, disk/config {_short(current)})"
        )
    created = marker.get("created", "unknown time")
    note = marker.get("postcondition", "unspecified")
    return f"done {created} ({note})"


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        default=os.environ.get(STATE_ENV, DEFAULT_STATE_DIR),
        help=f"marker directory (default $RIG_STATE_DIR or {DEFAULT_STATE_DIR})",
    )
    parser.add_argument(
        "--disk",
        default=None,
        help=f"boot disk override (default ${DISK_ENV})",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"rig config override (default ${CONFIG_ENV})",
    )


def _apply_overrides(args: argparse.Namespace) -> None:
    if args.disk is not None:
        os.environ[DISK_ENV] = args.disk
    if args.config is not None:
        os.environ[CONFIG_ENV] = args.config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen_parser = sub.add_parser("generation", help="print the current generation")
    _add_common(gen_parser)

    mark_parser = sub.add_parser("mark", help="write NAME's marker atomically")
    mark_parser.add_argument("name")
    mark_parser.add_argument("--postcondition", default="unspecified")
    _add_common(mark_parser)

    marked_parser = sub.add_parser(
        "marked", help="exit 0 only when NAME certifies the current generation"
    )
    marked_parser.add_argument("name")
    _add_common(marked_parser)

    status_parser = sub.add_parser("status", help="print NAME's state")
    status_parser.add_argument("name")
    _add_common(status_parser)

    args = parser.parse_args(argv)
    _apply_overrides(args)

    if args.command == "generation":
        print(generation(args.state_dir))
        return 0
    if args.command == "mark":
        gen = mark(args.state_dir, args.name, args.postcondition)
        print(gen)
        return 0
    if args.command == "marked":
        return 0 if marked(args.state_dir, args.name) else 1
    if args.command == "status":
        print(status(args.state_dir, args.name))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
