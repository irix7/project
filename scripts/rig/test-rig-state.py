#!/usr/bin/env python3
"""Unit tests for the generation-bound state markers in rig-state.py.

These are host-only: every case runs against a temporary disk image, config
and state directory. No live rig path, emulator, guest or network is touched.

Run: python3 scripts/rig/test-rig-state.py
"""

import importlib.util
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("rig_state", HERE / "rig-state.py")
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class RigStateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rig-state-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.disk = self.root / "disks" / "irix65.raw"
        self.disk.parent.mkdir()
        self.disk.write_bytes(b"\x00" * 512 + b"volume header" + b"\x00" * 1024)
        with open(self.disk, "r+b") as f:
            f.truncate(1024 * 1024)
        self.config = self.root / "iris.toml"
        self.config.write_text("headless = false\n")
        self.state_dir = str(self.root / "state")
        patcher = mock.patch.dict(
            os.environ,
            {"RIG_DISK": str(self.disk), "RIG_CONFIG": str(self.config)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def generation(self):
        return state.generation(
            self.state_dir, disk=str(self.disk), config=str(self.config)
        )


class GenerationTest(RigStateCase):
    def test_unchanged_disk_and_config_are_stable(self):
        self.assertEqual(self.generation(), self.generation())

    def test_replaced_disk_changes_generation(self):
        before = self.generation()
        self.disk.write_bytes(b"\x00" * 4096)
        self.assertNotEqual(before, self.generation())

    def test_removed_disk_changes_generation_and_is_stable(self):
        before = self.generation()
        self.disk.unlink()
        removed = self.generation()
        self.assertNotEqual(before, removed)
        self.assertEqual(removed, self.generation())

    def test_changed_config_changes_generation(self):
        before = self.generation()
        self.config.write_text("headless = true\n")
        self.assertNotEqual(before, self.generation())

    def test_removed_config_changes_generation(self):
        before = self.generation()
        self.config.unlink()
        self.assertNotEqual(before, self.generation())

    def test_writes_past_the_volume_header_do_not_change_generation(self):
        # The install writes inside the partitions; those writes must not
        # invalidate the phase markers of the install in progress.
        before = self.generation()
        with open(self.disk, "r+b") as f:
            f.seek(state.VOLUME_HEADER_BYTES + 1)
            f.write(b"install data")
        self.assertEqual(before, self.generation())

    def test_generation_is_bounded_for_a_large_sparse_disk(self):
        with open(self.disk, "r+b") as f:
            f.truncate(20 * 1024 * 1024 * 1024)
        before = self.generation()
        with open(self.disk, "r+b") as f:
            f.seek(1024 * 1024 * 1024)
            f.write(b"far away")
        self.assertEqual(before, self.generation())


class MarkerTest(RigStateCase):
    def mark(self, name, postcondition="test postcondition"):
        return state.mark(
            self.state_dir,
            name,
            postcondition,
        )

    def marked(self, name):
        return state.marked(self.state_dir, name)

    def test_mark_then_marked_is_true(self):
        self.mark("phase-a")
        self.assertTrue(self.marked("phase-a"))

    def test_absent_marker_is_not_marked(self):
        self.assertFalse(self.marked("phase-a"))

    def test_replaced_disk_invalidates_the_marker(self):
        self.mark("install")
        self.disk.write_bytes(b"\x00" * 4096)
        self.assertFalse(self.marked("install"))
        self.assertIn("stale", state.status(self.state_dir, "install"))

    def test_removed_disk_invalidates_the_marker(self):
        self.mark("install")
        self.disk.unlink()
        self.assertFalse(self.marked("install"))

    def test_changed_config_invalidates_the_marker(self):
        self.mark("verify")
        self.config.write_text("headless = true\n")
        self.assertFalse(self.marked("verify"))

    def test_legacy_timestamp_only_marker_never_certifies(self):
        os.makedirs(self.state_dir, exist_ok=True)
        Path(state.marker_path(self.state_dir, "label")).write_text(
            "2026-10-05T00:00:00\n"
        )
        self.assertFalse(self.marked("label"))
        self.assertIn("legacy", state.status(self.state_dir, "label"))

    def test_empty_or_corrupt_marker_never_certifies(self):
        os.makedirs(self.state_dir, exist_ok=True)
        Path(state.marker_path(self.state_dir, "label")).write_text("garbage\n")
        self.assertFalse(self.marked("label"))

    def test_status_pending_and_done(self):
        self.assertEqual(state.status(self.state_dir, "phase-a"), "pending")
        self.mark("phase-a", "NVRAM seeded and persisted")
        text = state.status(self.state_dir, "phase-a")
        self.assertIn("done ", text)
        self.assertIn("NVRAM seeded and persisted", text)

    def test_marker_carries_generation_created_and_postcondition(self):
        gen = self.mark("phase-a", "seed NVRAM")
        text = Path(state.marker_path(self.state_dir, "phase-a")).read_text()
        self.assertIn(f"generation: {gen}\n", text)
        self.assertIn("created: ", text)
        self.assertIn("postcondition: seed NVRAM\n", text)

    def test_rewriting_a_marker_is_atomic(self):
        self.mark("phase-a", "first")
        with mock.patch.object(state.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.mark("phase-a", "second")
        # The previous marker survives and the failed write leaves no temp file.
        self.assertTrue(self.marked("phase-a"))
        self.assertIn("first", state.status(self.state_dir, "phase-a"))
        leftovers = [
            name
            for name in os.listdir(self.state_dir)
            if name.startswith(".phase-a.done.")
        ]
        self.assertEqual(leftovers, [])

    def test_failed_first_write_leaves_no_partial_marker(self):
        with mock.patch.object(state.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.mark("install", "doomed")
        self.assertFalse(self.marked("install"))
        leftovers = [
            name
            for name in os.listdir(self.state_dir)
            if name.startswith(".install.done.")
        ]
        self.assertEqual(leftovers, [])

    def test_concurrent_readers_see_whole_markers_only(self):
        # A writer rewrites the marker while a reader polls it; because the
        # write is a rename, every observed snapshot must parse.
        stop = threading.Event()
        errors = []

        def writer():
            while not stop.is_set():
                state.mark(self.state_dir, "phase-a", "rewritten")

        def reader():
            while not stop.is_set():
                marker = state.read_marker(self.state_dir, "phase-a")
                if marker is not None and "postcondition" not in marker:
                    errors.append(marker)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])

    def test_mark_returns_the_bound_generation(self):
        gen = self.mark("phase-a")
        self.assertEqual(gen, self.generation())


class CliTest(RigStateCase):
    def run_cli(self, *args):
        return state.main(
            [
                *args,
                "--state-dir",
                self.state_dir,
                "--disk",
                str(self.disk),
                "--config",
                str(self.config),
            ]
        )

    def test_marked_exit_code_tracks_generation(self):
        self.assertEqual(self.run_cli("marked", "phase-a"), 1)
        self.assertEqual(
            self.run_cli("mark", "phase-a", "--postcondition", "cli"), 0
        )
        self.assertEqual(self.run_cli("marked", "phase-a"), 0)
        self.disk.write_bytes(b"\x00" * 2048)
        self.assertEqual(self.run_cli("marked", "phase-a"), 1)

    def test_status_never_fails_for_an_absent_marker(self):
        self.assertEqual(self.run_cli("status", "phase-a"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
