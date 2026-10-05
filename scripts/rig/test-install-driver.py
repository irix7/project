#!/usr/bin/env python3
"""Unit tests for the pure helpers in install-driver.py.

The install itself is an integration test against a live emulator; these cover
the one piece of logic that is easy to get subtly wrong and expensive to debug
mid-install: matching inst's disc requests against changer filenames. The
requests are the exact strings observed in the 6.5.7 install transcript.
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "install_driver", Path(__file__).with_name("install-driver.py")
)
drv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drv)

SOCKET = "/rig/iris.sock"
IC = "/rig/iris/target/release/iris-ci"

MEDIA = {
    "tools": "/rig/media/IRIX 6.5.7 Installation Tools and Overlays (1 of 2).iso",
    "foundation1": "/rig/media/IRIX 6.5 Foundation 1.iso",
    "foundation2": "/rig/media/IRIX 6.5 Foundation 2.iso",
    "overlays2": "/rig/media/IRIX 6.5.7 Overlays (2 of 2).iso",
}

REQUESTS = {
    "tools": "IRIX 6.5.7 Installation Tools and Overlays 1-of-2 02/00",
    "foundation1": "IRIX 6.5 FOUNDATION-1",
    "foundation2": "IRIX 6.5 FOUNDATION-2",
    "overlays2": "IRIX 6.5.7 Overlays 2-of-2 02/00",
}


class IcSocket(unittest.TestCase):
    """Every iris-ci call must name the selected socket in both argv and env."""

    def setUp(self):
        self.rig = drv.Rig(SOCKET, log_path=None, echo=False)
        self.calls = []

        def fake_run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        patcher = mock.patch.object(drv.subprocess, "run", side_effect=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_argv_carries_the_selected_socket(self):
        self.rig.run_ic(IC, "login")
        argv, _ = self.calls[0]
        self.assertEqual(argv, [IC, "--socket", SOCKET, "login"])

    def test_env_overrides_an_inherited_iris_socket(self):
        with mock.patch.dict(os.environ, {"IRIS_SOCKET": "/tmp/iris.sock"}):
            self.rig.run_ic(IC, "login")
        _, kwargs = self.calls[0]
        self.assertEqual(kwargs["env"]["IRIS_SOCKET"], SOCKET)
        self.assertNotEqual(kwargs["env"]["IRIS_SOCKET"], "/tmp/iris.sock")

    def test_run_ic_checks_and_keeps_caller_options(self):
        self.rig.run_ic(IC, "run", "uname -a", capture_output=True, text=True)
        argv, kwargs = self.calls[0]
        self.assertEqual(argv, [IC, "--socket", SOCKET, "run", "uname -a"])
        self.assertTrue(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])


class VerifySocket(unittest.TestCase):
    """The verify phase's login and run calls must pin the rig's socket."""

    def test_login_and_runs_pin_the_socket(self):
        rig = drv.Rig(SOCKET, log_path=None, echo=False)
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="out\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            evidence = os.path.join(tmp, "evidence.txt")
            with mock.patch.object(drv.Rig, "rpc"), \
                 mock.patch.object(drv.Rig, "send"), \
                 mock.patch.object(
                     drv.Rig, "expect", return_value=("", "console login:")
                 ), \
                 mock.patch.object(drv.subprocess, "run", side_effect=fake_run), \
                 mock.patch.dict(os.environ, {"IRIS_SOCKET": "/tmp/iris.sock"}):
                drv.verify(rig, tmp, IC, evidence)
            self.assertTrue(os.path.exists(evidence))

        self.assertTrue(
            any("login" in argv for argv, _ in calls), "no login call was made"
        )
        self.assertTrue(
            any("run" in argv for argv, _ in calls), "no run call was made"
        )
        for argv, kwargs in calls:
            self.assertEqual(argv[0], IC)
            self.assertEqual(argv[1:3], ["--socket", SOCKET])
            self.assertEqual(kwargs["env"]["IRIS_SOCKET"], SOCKET)


class DiscMatches(unittest.TestCase):
    def test_every_request_matches_its_own_disc(self):
        for key, request in REQUESTS.items():
            with self.subTest(disc=key):
                self.assertTrue(drv.disc_matches(request, MEDIA[key]))

    def test_no_request_matches_a_different_disc(self):
        for key, request in REQUESTS.items():
            for other, path in MEDIA.items():
                if other == key:
                    continue
                with self.subTest(request=key, mounted=other):
                    self.assertFalse(drv.disc_matches(request, path))

    def test_overlays_request_does_not_match_the_of_2_total_on_the_tools_disc(self):
        self.assertFalse(drv.disc_matches(REQUESTS["overlays2"], MEDIA["tools"]))

    def test_empty_mounted_path_never_matches(self):
        self.assertFalse(drv.disc_matches(REQUESTS["foundation1"], ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
