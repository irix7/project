#!/usr/bin/env python3
"""Confined extraction, attestation and atomic publication for the oracle
capture (issue #25).

The oracle capture used to be written straight over the previous one, so a
failed compile, transfer or extraction destroyed the only known-good sysroot.
This module makes a capture a *generation*: every retrieval lands in a fresh
build directory under ``$RIG_ORACLE_DIR/generations/``, is validated there,
and only a complete generation is published. Publication swaps the
``$RIG_ORACLE_DIR/sysroot`` symlink (and a ``current`` symlink to the
generation directory) with a rename, so the path a host link reads always
names a complete generation and a failure leaves the previous generation and
its symlinks untouched.

Two further guarantees live here:

  * extraction is confined. Absolute tar member names, ``..`` traversal,
    symlink targets that escape the tree and unsupported member types are
    refused before anything is written;
  * the attestation covers file types as well as content: regular files by
    SHA-256, directories, and every symlink by its immediate target and the
    complete chain it resolves to. A looping or escaping link fails
    validation. A dangling link is recorded with its target and an
    unresolved marker: IRIX itself ships links for uninstalled development
    subsets (for example X11 headers), and a faithful capture attests what
    the guest has rather than inventing the destination.

The format is documented in ``docs/oracle.md``; ``oracle.sh`` drives the CLI:

    oracle-capture.py capture-name --generation GEN [--stamp STAMP]
    oracle-capture.py finalise CAPTURE_DIR
    oracle-capture.py validate CAPTURE_DIR
    oracle-capture.py publish ORACLE_DIR CAPTURE_DIR NAME
"""

from __future__ import annotations

import argparse
import hashlib
import os
import posixpath
import re
import shutil
import stat
import sys
import tarfile
import time
from pathlib import Path

ATTESTATION_HEADER = "# rig sysroot attestation v2: <type> <sha256|target> <resolved> <path>"

# What the shell must retrieve before finalising. A missing or empty file here
# is a failed transfer, not a capture to publish.
REQUIRED_RETRIEVALS = (
    "environment.txt",
    "hello.o32",
    "hello.o32.o",
    "hello.o32.s",
    "hello.o32.build.log",
    "hello.o32.output",
    "hello.n32",
    "hello.n32.o",
    "hello.n32.s",
    "hello.n32.build.log",
    "hello.n32.output",
    "sysroot.tar.gz",
)

# The shared objects the manifest covers, relative to the capture directory.
MANIFEST_GLOBS = ("environment.txt", "hello*", "sysroot.tar.gz", "sysroot.manifest", "sysroot.sha256")

# Loose artefacts a pre-generation capture left at $RIG_ORACLE_DIR's top
# level. They are moved next to the archived legacy sysroot on first
# publication; a failure to move one is cosmetic and never blocks publication.
LEGACY_TOP_LEVEL = (
    "environment.txt",
    "hello*",
    "sysroot.tar.gz",
    "sysroot.manifest",
    "sysroot.sha256",
    "manifest.sha256",
)

MAX_LINK_DEPTH = 40
GENERATION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class CaptureError(Exception):
    pass


def valid_generation_name(name: str) -> bool:
    """A bare directory name that cannot traverse or hide."""
    if not name or not GENERATION_NAME_RE.match(name):
        return False
    if name.startswith(".") or ".." in name:
        return False
    return True


def capture_name(state_generation: str, stamp: str | None = None) -> str:
    """The directory name for a capture generation.

    The rig generation records which disk/config produced the capture; the
    UTC stamp keeps a recapture distinct so previous generations stay usable.
    """
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = re.sub(r"[^A-Za-z0-9._-]", "", state_generation)
    base = re.sub(r"\.{2,}", "", base).strip(".")[:64] or "unknown"
    name = f"sysroot-{base}-{stamp}"
    if not valid_generation_name(name):
        raise CaptureError(f"invalid capture generation name: {name!r}")
    return name


# ---------------------------------------------------------------------------
# Confined extraction
# ---------------------------------------------------------------------------


def _norm_rel(name: str) -> str | None:
    """Normalise an archive member name; None when it escapes or is empty."""
    if not name or "\x00" in name:
        return None
    if name.startswith("/") or name.startswith("\\"):
        return None
    normalised = posixpath.normpath(name)
    if normalised in (".", "..") or normalised.startswith("../"):
        return None
    if normalised.startswith("/"):
        return None
    return normalised


def _symlink_target_ok(member_name: str, target: str) -> bool:
    """True when an in-tree symlink target cannot escape the extraction root.

    The target is resolved relative to the member's directory; absolute
    targets and ``..`` traversal past the root are refused.
    """
    if not target or "\x00" in target:
        return False
    if target.startswith("/") or target.startswith("\\"):
        return False
    combined = posixpath.normpath(posixpath.join(posixpath.dirname(member_name), target))
    return combined not in (".", "..") and not combined.startswith("../")


def confined_extract(tar_path: str | Path, dest: str | Path) -> list[str]:
    """Extract a gzipped tar into ``dest``, refusing every escape.

    Returns the sorted list of member names. Raises CaptureError on a corrupt
    archive, an absolute or traversing name, a link that leaves the tree, or a
    member type the sysroot has no business carrying. The destination is
    created fresh and is the only thing written.
    """
    dest = Path(dest)
    names: list[str] = []
    seen: set[str] = set()
    try:
        archive = tarfile.open(tar_path, "r:gz")
    except (OSError, tarfile.TarError, EOFError) as e:
        raise CaptureError(f"cannot read {tar_path}: {e}") from e

    # Only a readable archive may touch the destination, so a corrupt
    # download leaves no half-created tree behind.
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    try:
        for member in archive:
            name = _norm_rel(member.name)
            if name is None:
                raise CaptureError(f"tar member escapes the extraction root: {member.name!r}")
            if name in seen:
                raise CaptureError(f"tar member appears twice: {name!r}")
            seen.add(name)
            names.append(name)
            target = dest / name

            try:
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isreg():
                    source = archive.extractfile(member)
                    if source is None:
                        raise CaptureError(f"tar member has no data: {name!r}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, "wb") as f:
                        shutil.copyfileobj(source, f)
                elif member.issym():
                    if not _symlink_target_ok(name, member.linkname):
                        raise CaptureError(
                            f"tar symlink escapes the extraction root: {name!r} -> {member.linkname!r}"
                        )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(member.linkname, target)
                elif member.islnk():
                    link = _norm_rel(member.linkname)
                    if link is None:
                        raise CaptureError(
                            f"tar hard link escapes the extraction root: {name!r} -> {member.linkname!r}"
                        )
                    source = dest / link
                    if not source.exists():
                        raise CaptureError(f"tar hard link target is missing: {name!r} -> {link!r}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(source, target)
                else:
                    raise CaptureError(
                        f"tar member has an unsupported type ({member.type!r}): {name!r}"
                    )
            except CaptureError:
                raise
            except (OSError, tarfile.TarError, EOFError) as e:
                raise CaptureError(f"cannot extract {name!r}: {e}") from e
    except (tarfile.TarError, EOFError) as e:
        raise CaptureError(f"cannot read {tar_path}: {e}") from e
    finally:
        archive.close()
    return sorted(names)


# ---------------------------------------------------------------------------
# Attestation
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_link(root: Path, rel: str, limit: int = MAX_LINK_DEPTH) -> tuple[str | None, str | None]:
    """Follow a symlink chain and classify what it terminates at.

    Returns ``(resolved, error)``. On success ``resolved`` is the in-tree path
    the chain terminates at and ``error`` is None. ``error`` is "dangling"
    when the chain is well-formed but its final target is absent (IRIX ships
    such links, e.g. for uninstalled X11/GL development subsets, and a
    faithful capture records them), "loop" when a path repeats or the depth
    limit is hit, and "escape" for an absolute target or one that leaves the
    tree. A chain through intermediate symlinks is resolved in full.
    """
    seen: set[str] = set()
    current = rel
    for _ in range(limit):
        if current in seen:
            return None, "loop"
        seen.add(current)
        path = root / current
        if not path.is_symlink():
            return (current, None) if path.exists() else (None, "dangling")
        target = os.readlink(path)
        if target.startswith("/") or target.startswith("\\"):
            return None, "escape"
        current = posixpath.normpath(
            posixpath.join(posixpath.dirname(current), target)
        )
        if current in (".", "..") or current.startswith("../"):
            return None, "escape"
    return None, "loop"


def _link_line(root: Path, rel: str) -> tuple[str, str | None]:
    """The attestation line for a symlink, and its unresolved error or None.

    A dangling link is recorded with ``-`` in the resolved column: the
    immediate target is attested even when IRIX's own tree does not carry its
    destination.
    """
    target = os.readlink(root / rel)
    resolved, error = resolve_link(root, rel)
    if resolved is None:
        return f"l {target} - {rel}", error
    return f"l {target} {resolved} {rel}", None


def attest_tree(root: str | Path) -> list[str]:
    """Every entry under ``root`` as sorted attestation lines.

    ``f`` carries the regular file's content digest, ``d`` a directory, ``l``
    a symlink's immediate target and its complete resolved chain. Anything
    else is recorded as ``x`` and fails validation.
    """
    root = Path(root)
    entries: list[tuple[str, str]] = []
    problems: list[str] = []

    def record(rel: str, line: str) -> None:
        entries.append((rel, line))

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for name in list(dirnames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                line, error = _link_line(root, rel)
                if error not in (None, "dangling"):
                    problems.append(f"{error} symlink: {rel}")
                record(rel, line)
                dirnames.remove(name)
            else:
                record(rel, f"d {rel}")
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                line, error = _link_line(root, rel)
                if error not in (None, "dangling"):
                    problems.append(f"{error} symlink: {rel}")
                record(rel, line)
            elif stat.S_ISREG(st.st_mode):
                record(rel, f"f {_sha256_file(path)} {rel}")
            else:
                problems.append(f"unsupported file type: {rel}")
                record(rel, f"x {rel}")

    lines = [ATTESTATION_HEADER] + [line for _, line in sorted(entries)]
    if problems:
        raise CaptureError("; ".join(sorted(set(problems))))
    return lines


# ---------------------------------------------------------------------------
# Capture directory assembly and validation
# ---------------------------------------------------------------------------


def _retrieval_problems(capture: Path) -> list[str]:
    problems = []
    for name in REQUIRED_RETRIEVALS:
        path = capture / name
        if not path.is_file():
            problems.append(f"missing retrieval: {name}")
        elif path.stat().st_size == 0:
            problems.append(f"empty retrieval: {name}")
    return problems


def write_tar_manifest(capture: Path) -> None:
    with tarfile.open(capture / "sysroot.tar.gz", "r:gz") as archive:
        names = archive.getnames()
    (capture / "sysroot.manifest").write_text("\n".join(names) + "\n")


def write_attestation(capture: Path) -> list[str]:
    lines = attest_tree(capture / "sysroot")
    (capture / "sysroot.sha256").write_text("\n".join(lines) + "\n")
    return lines


def _manifest_files(capture: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in MANIFEST_GLOBS:
        files.extend(path for path in capture.glob(pattern) if path.is_file())
    return sorted(set(files), key=lambda p: p.name)


def write_manifest(capture: Path) -> None:
    lines = []
    for path in _manifest_files(capture):
        lines.append(f"{_sha256_file(path)}  {path.name}")
    (capture / "manifest.sha256").write_text("\n".join(lines) + "\n")


def _manifest_problems(capture: Path) -> list[str]:
    manifest = capture / "manifest.sha256"
    if not manifest.is_file():
        return ["missing manifest.sha256"]
    problems: list[str] = []
    listed: set[str] = set()
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if not name:
            problems.append(f"unparseable manifest line: {line!r}")
            continue
        listed.add(name)
        path = capture / name
        if not path.is_file():
            problems.append(f"manifest names a missing file: {name}")
            continue
        if _sha256_file(path) != digest:
            problems.append(f"manifest digest mismatch: {name}")
    expected = {path.name for path in _manifest_files(capture)}
    for name in sorted(expected - listed):
        problems.append(f"manifest omits: {name}")
    return problems


def _attestation_problems(capture: Path) -> list[str]:
    stored_path = capture / "sysroot.sha256"
    if not stored_path.is_file():
        return ["missing sysroot.sha256"]
    stored = stored_path.read_text().splitlines()
    try:
        actual = attest_tree(capture / "sysroot")
    except CaptureError as e:
        return [str(e)]
    if stored == actual:
        return []
    problems = ["sysroot attestation is stale or incomplete"]
    only_stored = [line for line in stored if line not in actual]
    only_actual = [line for line in actual if line not in stored]
    for line in only_stored[:5]:
        problems.append(f"  attested but absent: {line}")
    for line in only_actual[:5]:
        problems.append(f"  present but un-attested: {line}")
    return problems


def validate_retrievals(capture_dir: str | Path) -> list[str]:
    return _retrieval_problems(Path(capture_dir))


def validate_capture(capture_dir: str | Path, *, verify_digests: bool = True) -> list[str]:
    """Problems with a finalised capture directory; empty means complete."""
    capture = Path(capture_dir)
    problems = _retrieval_problems(capture)
    for name in ("sysroot.manifest", "sysroot.sha256", "manifest.sha256"):
        if not (capture / name).is_file():
            problems.append(f"missing {name}")
    root = capture / "sysroot"
    if not root.is_dir():
        problems.append("missing extracted sysroot/ tree")
    if problems or not verify_digests:
        return problems
    problems.extend(_attestation_problems(capture))
    problems.extend(_manifest_problems(capture))
    return problems


def finalise(capture_dir: str | Path) -> None:
    """Turn a retrieval directory into a validated capture generation.

    Validates every transfer first, extracts the tar in confinement, writes
    the tar manifest, the sysroot attestation and the capture manifest, and
    finishes with the same full validation publication will require.
    """
    capture = Path(capture_dir)
    if not capture.is_dir():
        raise CaptureError(f"capture directory does not exist: {capture}")
    problems = _retrieval_problems(capture)
    if problems:
        raise CaptureError("incomplete capture: " + "; ".join(problems))

    confined_extract(capture / "sysroot.tar.gz", capture / "sysroot")
    write_tar_manifest(capture)
    write_attestation(capture)
    write_manifest(capture)

    problems = validate_capture(capture, verify_digests=True)
    if problems:
        raise CaptureError("capture failed validation: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Atomic publication
# ---------------------------------------------------------------------------


def _symlink_temp(target: str, link_path: Path) -> Path:
    tmp = link_path.parent / f".{link_path.name}.tmp.{os.getpid()}"
    for suffix in range(100):
        if suffix:
            tmp = link_path.parent / f".{link_path.name}.tmp.{os.getpid()}.{suffix}"
        try:
            os.symlink(target, tmp)
            return tmp
        except FileExistsError:
            continue
    raise CaptureError(f"cannot create a temporary symlink for {link_path}")


def _archive_legacy(oracle: Path, generations: Path, link_path: Path) -> Path | None:
    """Move a pre-generation path out of the way so the symlink can land.

    A real directory cannot be replaced by a rename, so the legacy sysroot is
    moved intact into ``generations/sysroot-legacy-<stamp>`` first. The loose
    legacy evidence files move with it when that succeeds; a failure to move
    one leaves it in place and never blocks publication.
    """
    if not link_path.exists() and not link_path.is_symlink():
        return None
    if link_path.is_symlink():
        return None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = generations / f"sysroot-legacy-{stamp}"
    suffix = 1
    while dest.exists() or dest.is_symlink():
        dest = generations / f"sysroot-legacy-{stamp}-{suffix}"
        suffix += 1
    dest.mkdir()
    os.rename(link_path, dest / link_path.name)
    for pattern in LEGACY_TOP_LEVEL:
        for path in oracle.glob(pattern):
            try:
                shutil.move(str(path), str(dest / path.name))
            except OSError:
                pass
    return dest


def publish(oracle_dir: str | Path, capture_dir: str | Path, name: str) -> None:
    """Validate a capture and make it the current generation, atomically.

    The directory is renamed into ``generations/<name>`` first, then the one
    authoritative ``sysroot`` symlink is swapped with a single rename. A
    failure before that swap leaves the previous generation, symlink and
    legacy evidence in place; there is no second link to fall out of step.
    """
    if not valid_generation_name(name):
        raise CaptureError(f"invalid capture generation name: {name!r}")
    capture = Path(capture_dir)
    oracle = Path(oracle_dir)
    generations = oracle / "generations"
    final = generations / name
    if final.exists() or final.is_symlink():
        raise CaptureError(f"capture generation already exists: {final}")

    problems = validate_capture(capture, verify_digests=True)
    if problems:
        raise CaptureError("refusing to publish an invalid capture: " + "; ".join(problems))

    generations.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(capture, final)
    except OSError as e:
        raise CaptureError(
            f"cannot publish {capture} as {final}: {e}; the build directory must "
            "live under $RIG_ORACLE_DIR/generations on the same filesystem"
        ) from e

    sysroot_link = oracle / "sysroot"
    tmp_sysroot = _symlink_temp(f"generations/{name}/sysroot", sysroot_link)

    try:
        legacy = _archive_legacy(oracle, generations, sysroot_link)
        try:
            os.replace(tmp_sysroot, sysroot_link)
        except OSError:
            if legacy is not None:
                # Best effort: restore the legacy baseline to its published name.
                try:
                    os.rename(legacy, sysroot_link)
                except OSError:
                    pass
            raise
    finally:
        try:
            os.unlink(tmp_sysroot)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    name_parser = sub.add_parser("capture-name", help="print a unique generation name")
    name_parser.add_argument("--generation", required=True)
    name_parser.add_argument("--stamp", default=None)

    finalise_parser = sub.add_parser("finalise", help="validate and attest a capture")
    finalise_parser.add_argument("capture_dir")

    validate_parser = sub.add_parser("validate", help="report capture problems")
    validate_parser.add_argument("capture_dir")

    publish_parser = sub.add_parser("publish", help="atomically publish a capture")
    publish_parser.add_argument("oracle_dir")
    publish_parser.add_argument("capture_dir")
    publish_parser.add_argument("name")

    args = parser.parse_args(argv)

    if args.command == "capture-name":
        try:
            print(capture_name(args.generation, args.stamp))
        except CaptureError as e:
            print(f"oracle-capture: {e}", file=sys.stderr)
            return 1
        return 0

    try:
        if args.command == "finalise":
            finalise(args.capture_dir)
        elif args.command == "validate":
            problems = validate_capture(args.capture_dir, verify_digests=True)
            if problems:
                for problem in problems:
                    print(f"oracle-capture: {problem}", file=sys.stderr)
                return 1
        elif args.command == "publish":
            publish(args.oracle_dir, args.capture_dir, args.name)
    except CaptureError as e:
        print(f"oracle-capture: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
