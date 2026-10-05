#!/usr/bin/env python3
"""Host-only tests for confined extraction, attestation and atomic capture
publication in oracle-capture.py (issue #25).

Every case runs against temporary directories; no emulator, guest, network or
live rig path is touched. A capture is simulated by writing the files the
shell would have retrieved and building the sysroot tarball in Python.

Run: python3 scripts/rig/test-oracle-capture.py
"""

import importlib.util
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location(
    "oracle_capture", HERE / "oracle-capture.py"
)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def add_bytes(archive, name, data=b"data", mode=0o644):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    archive.addfile(info, io.BytesIO(data))


def add_dir(archive, name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    archive.addfile(info)


def add_symlink(archive, name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    archive.addfile(info)


def add_hardlink(archive, name, target):
    info = tarfile.TarInfo(name)
    info.type = tarfile.LNKTYPE
    info.linkname = target
    archive.addfile(info)


def write_tar(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for kind, name, payload in entries:
            if kind == "f":
                add_bytes(archive, name, payload)
            elif kind == "d":
                add_dir(archive, name)
            elif kind == "l":
                add_symlink(archive, name, payload)
            elif kind == "h":
                add_hardlink(archive, name, payload)
            else:
                raise AssertionError(kind)


class CaptureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oracle-capture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def extract(self, entries, name="sysroot.tar.gz"):
        tar = self.root / name
        write_tar(tar, entries)
        dest = self.root / "tree"
        return tar, dest, capture.confined_extract(tar, dest)

    def make_capture(self, tar_entries=None, name="build"):
        """A capture directory with every retrieval the shell would have made."""
        cap = self.root / name
        cap.mkdir()
        for retrieval in capture.REQUIRED_RETRIEVALS:
            if retrieval == "sysroot.tar.gz":
                write_tar(
                    cap / retrieval,
                    tar_entries
                    if tar_entries is not None
                    else [("f", "usr/include/stdio.h", b"#define STDIO\n")],
                )
            else:
                (cap / retrieval).write_bytes(f"{retrieval}\n".encode())
        return cap


class CaptureNameTest(unittest.TestCase):
    def test_name_is_deterministic_and_generation_bound(self):
        self.assertEqual(
            capture.capture_name("abc123", "20261006T000000Z"),
            "sysroot-abc123-20261006T000000Z",
        )

    def test_name_rejects_a_sneaky_generation(self):
        name = capture.capture_name("../../etc", "20261006T000000Z")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)


class ConfinedExtractTest(CaptureCase):
    def test_round_trip_regular_files_and_directories(self):
        tar, dest, names = self.extract(
            [
                ("d", "usr", None),
                ("d", "usr/include", None),
                ("f", "usr/include/stdio.h", b"header\n"),
            ]
        )
        self.assertEqual(names, ["usr", "usr/include", "usr/include/stdio.h"])
        self.assertEqual((dest / "usr/include/stdio.h").read_bytes(), b"header\n")

    def test_absolute_member_name_is_refused(self):
        with self.assertRaises(capture.CaptureError):
            self.extract([("f", "/etc/passwd", b"x")])

    def test_traversal_member_name_is_refused(self):
        with self.assertRaises(capture.CaptureError):
            self.extract([("f", "../escape", b"x")])

    def test_escaping_symlink_is_refused(self):
        with self.assertRaises(capture.CaptureError):
            self.extract([("l", "link", "../../outside")])

    def test_absolute_symlink_is_refused(self):
        with self.assertRaises(capture.CaptureError):
            self.extract([("l", "link", "/etc/passwd")])

    def test_escaping_hard_link_is_refused(self):
        with self.assertRaises(capture.CaptureError):
            self.extract([("h", "link", "../outside")])

    def test_unsupported_member_type_is_refused(self):
        tar = self.root / "fifo.tar.gz"
        with tarfile.open(tar, "w:gz") as archive:
            info = tarfile.TarInfo("pipe")
            info.type = tarfile.FIFOTYPE
            archive.addfile(info)
        with self.assertRaises(capture.CaptureError):
            capture.confined_extract(tar, self.root / "tree")

    def test_corrupt_archive_is_refused(self):
        tar = self.root / "sysroot.tar.gz"
        tar.write_bytes(b"this is not a gzip stream")
        with self.assertRaises(capture.CaptureError):
            capture.confined_extract(tar, self.root / "tree")

    def test_confined_relative_symlink_and_hard_link_extract(self):
        _, dest, _ = self.extract(
            [
                ("f", "real", b"payload\n"),
                ("l", "sym", "real"),
                ("h", "hard", "real"),
            ]
        )
        self.assertEqual(os.readlink(dest / "sym"), "real")
        self.assertEqual((dest / "sym").read_bytes(), b"payload\n")
        self.assertEqual((dest / "hard").read_bytes(), b"payload\n")


class AttestationTest(CaptureCase):
    def build_tree(self):
        root = self.root / "tree"
        (root / "usr/include").mkdir(parents=True)
        (root / "usr/include/stdio.h").write_text("header\n")
        (root / "lib").mkdir()
        (root / "lib/libx.so.1.0").write_text("object\n")
        os.symlink("libx.so.1.0", root / "lib/libx.so.1")
        os.symlink("libx.so.1", root / "lib/libx.so")
        return root

    def test_attestation_covers_content_types_and_targets(self):
        import hashlib

        root = self.build_tree()
        lines = capture.attest_tree(root)
        self.assertIn("d usr", lines)
        self.assertIn("d usr/include", lines)
        self.assertIn("d lib", lines)
        digest = hashlib.sha256(b"object\n").hexdigest()
        self.assertIn(f"f {digest} lib/libx.so.1.0", lines)
        self.assertIn(
            "l libx.so.1 lib/libx.so.1.0 lib/libx.so", lines
        )
        self.assertIn(
            "l libx.so.1.0 lib/libx.so.1.0 lib/libx.so.1", lines
        )

    def test_complete_chain_resolves_to_the_regular_file(self):
        root = self.build_tree()
        self.assertEqual(
            capture.resolve_link(root, "lib/libx.so"), ("lib/libx.so.1.0", None)
        )

    def test_dangling_symlink_is_attested_unresolved(self):
        root = self.root / "tree"
        root.mkdir()
        os.symlink("missing", root / "dangling")
        lines = capture.attest_tree(root)
        self.assertIn("l missing - dangling", lines)

    def test_looping_symlinks_fail_attestation(self):
        root = self.root / "tree"
        root.mkdir()
        os.symlink("b", root / "a")
        os.symlink("a", root / "b")
        with self.assertRaises(capture.CaptureError):
            capture.attest_tree(root)


class FinaliseTest(CaptureCase):
    def test_finalise_produces_a_validated_capture(self):
        cap = self.make_capture()
        capture.finalise(cap)
        self.assertEqual(capture.validate_capture(cap), [])
        self.assertTrue((cap / "sysroot/usr/include/stdio.h").is_file())
        self.assertTrue((cap / "sysroot.manifest").is_file())
        self.assertTrue((cap / "sysroot.sha256").is_file())
        self.assertTrue((cap / "manifest.sha256").is_file())

    def test_missing_retrieval_fails_before_any_artefact_is_written(self):
        cap = self.make_capture()
        (cap / "hello.n32").unlink()
        with self.assertRaises(capture.CaptureError):
            capture.finalise(cap)
        self.assertFalse((cap / "sysroot").exists())
        self.assertFalse((cap / "manifest.sha256").exists())

    def test_empty_retrieval_fails(self):
        cap = self.make_capture()
        (cap / "environment.txt").write_bytes(b"")
        with self.assertRaises(capture.CaptureError):
            capture.finalise(cap)

    def test_corrupt_tar_fails_and_writes_nothing(self):
        cap = self.make_capture()
        (cap / "sysroot.tar.gz").write_bytes(b"garbage")
        with self.assertRaises(capture.CaptureError):
            capture.finalise(cap)
        self.assertFalse((cap / "sysroot").exists())

    def test_escaped_member_fails_and_writes_nothing(self):
        cap = self.make_capture([("f", "../escape", b"x")])
        with self.assertRaises(capture.CaptureError):
            capture.finalise(cap)
        self.assertFalse((cap / "manifest.sha256").exists())

    def test_dangling_symlink_is_attested_and_validates(self):
        cap = self.make_capture()
        (cap / "sysroot").mkdir()
        os.symlink("missing", cap / "sysroot/dangling")
        (cap / "sysroot.tar.gz").write_bytes(b"placeholder")
        capture.write_attestation(cap)
        attestation = (cap / "sysroot.sha256").read_text()
        self.assertIn("l missing - dangling", attestation)

    def test_tampering_after_finalise_is_caught_by_validation(self):
        cap = self.make_capture()
        capture.finalise(cap)
        (cap / "sysroot/extra.h").write_text("inserted\n")
        problems = capture.validate_capture(cap)
        self.assertTrue(problems)
        self.assertTrue(any("un-attested" in p for p in problems))

    def test_truncated_download_is_caught_by_validation(self):
        cap = self.make_capture()
        capture.finalise(cap)
        (cap / "hello.n32").write_bytes(b"tampered")
        problems = capture.validate_capture(cap)
        self.assertTrue(any("manifest digest mismatch" in p for p in problems))


class PublishTest(CaptureCase):
    def setUp(self):
        super().setUp()
        self.oracle = self.root / "oracle"
        self.oracle.mkdir()

    def complete_capture(self, name="build"):
        cap = self.make_capture(name=name)
        capture.finalise(cap)
        return cap

    def test_publish_swaps_symlinks_and_keeps_previous_generations(self):
        previous = self.oracle / "generations" / "sysroot-old"
        (previous / "sysroot").mkdir(parents=True)
        (previous / "sysroot/libc.so").write_text("old\n")
        os.symlink("generations/sysroot-old/sysroot", self.oracle / "sysroot")

        cap = self.complete_capture()
        capture.publish(self.oracle, cap, "sysroot-new-stamp")

        self.assertTrue((self.oracle / "sysroot").is_symlink())
        self.assertEqual(
            os.readlink(self.oracle / "sysroot"),
            "generations/sysroot-new-stamp/sysroot",
        )
        self.assertTrue(
            (self.oracle / "generations/sysroot-new-stamp/sysroot").is_dir()
        )
        self.assertTrue(previous.is_dir(), "the previous generation was destroyed")

    def test_failed_publication_leaves_the_previous_generation_usable(self):
        previous = self.oracle / "generations" / "sysroot-old"
        (previous / "sysroot").mkdir(parents=True)
        os.symlink("generations/sysroot-old/sysroot", self.oracle / "sysroot")

        cap = self.make_capture()
        (cap / "hello.o32").unlink()

        with self.assertRaises(capture.CaptureError):
            capture.publish(self.oracle, cap, "sysroot-new-stamp")

        self.assertEqual(
            os.readlink(self.oracle / "sysroot"),
            "generations/sysroot-old/sysroot",
        )
        self.assertTrue(cap.exists(), "the failed build directory was not left for inspection")
        self.assertFalse(
            (self.oracle / "generations/sysroot-new-stamp").exists()
        )

    def test_publish_refuses_an_existing_generation_name(self):
        cap = self.complete_capture()
        capture.publish(self.oracle, cap, "sysroot-new-stamp")
        other = self.complete_capture(name="build2")
        with self.assertRaises(capture.CaptureError):
            capture.publish(self.oracle, other, "sysroot-new-stamp")

    def test_legacy_directory_is_archived_before_the_symlink_lands(self):
        legacy = self.oracle / "sysroot"
        legacy.mkdir()
        (legacy / "libc.so").write_text("legacy\n")
        (self.oracle / "environment.txt").write_text("legacy environment\n")

        cap = self.complete_capture()
        capture.publish(self.oracle, cap, "sysroot-new-stamp")

        self.assertEqual(
            os.readlink(self.oracle / "sysroot"),
            "generations/sysroot-new-stamp/sysroot",
        )
        archived = list((self.oracle / "generations").glob("sysroot-legacy-*"))
        self.assertEqual(len(archived), 1)
        self.assertEqual((archived[0] / "sysroot/libc.so").read_text(), "legacy\n")
        self.assertEqual(
            (archived[0] / "environment.txt").read_text(), "legacy environment\n"
        )
        self.assertFalse((self.oracle / "environment.txt").exists())

    def test_publish_rejects_a_generation_name_with_a_path(self):
        cap = self.complete_capture()
        with self.assertRaises(capture.CaptureError):
            capture.publish(self.oracle, cap, "../escape")


if __name__ == "__main__":
    unittest.main(verbosity=2)
