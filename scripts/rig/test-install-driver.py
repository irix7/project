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
            return subprocess.CompletedProcess(
                argv, 0, stdout="IRIX 6.5.7m (maintenance)\n", stderr=""
            )

        with tempfile.TemporaryDirectory() as tmp:
            evidence = os.path.join(tmp, "evidence.txt")
            with mock.patch.object(drv.Rig, "rpc"), \
                 mock.patch.object(drv.Rig, "send"), \
                 mock.patch.object(
                     drv.Rig, "expect", return_value=("", "console login:")
                 ), \
                 mock.patch.object(drv.rig_state, "mark"), \
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


class ClassifyInstallTranscript(unittest.TestCase):
    """The whole `go` transcript decides: errors beat a success line."""

    def test_clean_success(self):
        self.assertEqual(
            drv.classify_install_transcript(
                "Installing .. 100% Done.\n"
                "Installations and removals were successful.\n"
            ),
            "success",
        )

    def test_mixed_error_then_success_fails(self):
        text = (
            "ERROR: incompatible subsystems were selected\n"
            "Installations and removals were successful.\n"
        )
        self.assertEqual(drv.classify_install_transcript(text), "error")

    def test_mixed_conflict_then_success_fails(self):
        text = (
            "Conflicts must be resolved before continuing.\n"
            "Installations and removals were successful.\n"
        )
        self.assertEqual(drv.classify_install_transcript(text), "conflict")

    def test_success_before_a_later_error_also_fails(self):
        text = (
            "Installations and removals were successful.\n"
            "exitop: ERROR: cannot remove /usr/lib/thing\n"
        )
        self.assertEqual(drv.classify_install_transcript(text), "error")

    def test_only_an_explicitly_allowlisted_line_is_dropped(self):
        harmless = ("ERROR: the CD label differs from the medium",)
        text = (
            "ERROR: the CD label differs from the medium\n"
            "Installations and removals were successful.\n"
        )
        # The default (empty) allowlist fails closed...
        self.assertEqual(drv.classify_install_transcript(text), "error")
        # ...and a specific, cited harmless line is the only way past it.
        self.assertEqual(
            drv.classify_install_transcript(text, harmless), "success"
        )
        mixed = (
            "ERROR: the CD label differs from the medium\n"
            "ERROR: cannot create /usr/lib/thing\n"
            "Installations and removals were successful.\n"
        )
        self.assertEqual(drv.classify_install_transcript(mixed, harmless), "error")

    def test_quiet_transcript_is_unclassified(self):
        self.assertIsNone(
            drv.classify_install_transcript("Installing/removing files .. 47%")
        )

    def test_has_failure_matches_the_classifier(self):
        self.assertTrue(drv.has_failure("ERROR: nope"))
        self.assertTrue(drv.has_failure("Conflicts must be resolved"))
        self.assertFalse(drv.has_failure("Installations and removals were successful"))


class MaintenanceStream(unittest.TestCase):
    def test_versions_output_naming_6_5_7m_verifies(self):
        self.assertTrue(
            drv.maintenance_stream_verified(
                "Idea (\"versions\") 6.5.7m\nIRIX 6.5.7m (maintenance)\n"
            )
        )

    def test_plain_6_5_7_without_the_m_is_refused(self):
        self.assertFalse(drv.maintenance_stream_verified("IRIX 6.5.7\n"))

    def test_a_longer_numeric_suffix_does_not_verify(self):
        self.assertFalse(drv.maintenance_stream_verified("IRIX 6.5.7mX\n"))
        self.assertFalse(drv.maintenance_stream_verified("IRIX 16.5.7m\n"))

    def test_empty_output_is_refused(self):
        self.assertFalse(drv.maintenance_stream_verified(""))


class StreamSelectionTest(unittest.TestCase):
    MENU = (
        "Install software from:\n"
        "  1.  Maintenance stream (IRIX 6.5.7m)\n"
        "  2.  Feature stream (IRIX 6.5.7)\n"
        "Please enter a choice:\n"
    )

    def test_option_number_is_parsed_not_assumed(self):
        self.assertEqual(drv.stream_choice_number(self.MENU), "1")
        renumbered = self.MENU.replace("1.  Maintenance", "7.  Maintenance")
        self.assertEqual(drv.stream_choice_number(renumbered), "7")

    def test_menu_without_a_maintenance_option_has_no_answer(self):
        feature_only = (
            "  1.  Feature stream (IRIX 6.5.7)\n"
            "Please enter a choice:\n"
        )
        self.assertIsNone(drv.stream_choice_number(feature_only))
        selection = drv.StreamSelection()
        with self.assertRaises(drv.RigError):
            selection.answer(feature_only)
        self.assertFalse(selection.selected)

    def test_repeated_rejection_fails_instead_of_looping(self):
        selection = drv.StreamSelection(max_consecutive=2)
        selection.answer(self.MENU)
        selection.answer(self.MENU)
        with self.assertRaises(drv.RigError):
            selection.answer(self.MENU)
        self.assertTrue(selection.selected)

    def test_a_completed_scan_resets_the_retry_budget(self):
        selection = drv.StreamSelection(max_consecutive=1)
        selection.answer(self.MENU)
        selection.progress()
        selection.answer(self.MENU)
        self.assertTrue(selection.selected)

    def test_stream_menu_detection_is_not_fooled_by_other_choices(self):
        self.assertTrue(drv.is_stream_menu(self.MENU))
        self.assertFalse(drv.is_stream_menu("Please enter a choice:"))


class DiscScanTest(unittest.TestCase):
    def test_a_disc_with_no_tree_at_all_fails(self):
        error = drv.disc_scan_error(
            "IRIX 6.5 Foundation 1.iso",
            [("/CDROM/dist", False), ("/CDROM/dist/unbundled", False)],
        )
        self.assertIsNotNone(error)
        self.assertIn("Foundation 1", error)

    def test_modern_layout_satisfies_the_disc(self):
        self.assertIsNone(
            drv.disc_scan_error("disc", [("/CDROM/dist", True), ("/CDROM/dist/unbundled", False)])
        )

    def test_overlays_alternate_layout_satisfies_the_disc(self):
        self.assertIsNone(
            drv.disc_scan_error("disc", [("/CDROM/dist", False), ("/CDROM/dist/unbundled", True)])
        )


class Result:
    def __init__(self, stdout):
        self.stdout = stdout


class FakeRig:
    """A scripted console: pre-canned expect results and recorded sends."""

    def __init__(self, prompts, versions="Idea (\"versions\") 6.5.7m\n"):
        self.prompts = list(prompts)
        self.versions = versions
        self.sent = []
        self.ic_calls = []
        self.expect_calls = []

    def rpc(self, cmd, **args):
        return None

    def send(self, text, cr=True):
        self.sent.append(text)

    def run_ic(self, ic, *args, **kwargs):
        self.ic_calls.append(args)
        if args and args[0] == "run" and any("versions eoe" in a for a in args):
            return Result(self.versions)
        return Result("# uname output\n")

    def expect(self, patterns, timeout=None, earliest=False):
        self.expect_calls.append((patterns, earliest))
        if not self.prompts:
            raise drv.RigError("test ran out of scripted prompts")
        return self.prompts.pop(0)


class ResumeToLogin(unittest.TestCase):
    def test_shell_prompt_resumes_without_touching_iris_ci(self):
        rig = FakeRig([("# ", "# ")])
        drv.resume_to_login(rig, IC)
        self.assertEqual(rig.ic_calls, [])
        self.assertEqual(rig.sent, [])

    def test_login_prompt_logs_in(self):
        rig = FakeRig([("", "console login:")])
        drv.resume_to_login(rig, IC)
        self.assertEqual([args[-1] for args in rig.ic_calls], ["login"])

    def test_prom_state_boots_and_logs_in(self):
        rig = FakeRig([("", "Option?")])
        drv.resume_to_login(rig, IC)
        self.assertEqual([args[-1] for args in rig.ic_calls], ["boot", "login"])

    def test_sash_repair_prompt_is_answered_then_resumed(self):
        rig = FakeRig([("", "Enter 'c' to continue"), ("", "login:")])
        drv.resume_to_login(rig, IC)
        self.assertIn("c", rig.sent)
        self.assertEqual([args[-1] for args in rig.ic_calls], ["login"])


class QuitAndRestart(unittest.TestCase):
    def test_reaches_the_restart_prompt_then_answers(self):
        rig = mock.Mock()
        drv.quit_and_restart(rig)
        self.assertEqual(
            rig.send.call_args_list, [mock.call("quit"), mock.call("y")]
        )
        rig.expect_re.assert_called_once()
        self.assertIn("restart", rig.expect_re.call_args.args[0])

    def test_a_missing_restart_prompt_raises_before_answering(self):
        rig = mock.Mock()
        rig.expect_re.side_effect = drv.RigError("timed out waiting for restart")
        with self.assertRaises(drv.RigError):
            drv.quit_and_restart(rig)
        self.assertEqual(rig.send.call_args_list, [mock.call("quit")])


class VerifyPostcondition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = os.path.join(self.tmp.name, "evidence.txt")

    def test_wrong_release_writes_no_evidence_and_no_marker(self):
        rig = FakeRig([("", "# ")], versions="IRIX 6.5.7\n")
        with mock.patch.object(drv.rig_state, "mark") as mark:
            with self.assertRaises(drv.RigError):
                drv.verify(rig, self.tmp.name, IC, self.evidence)
        self.assertFalse(os.path.exists(self.evidence))
        mark.assert_not_called()

    def test_verified_release_records_evidence_then_marks(self):
        rig = FakeRig([("", "# ")], versions="IRIX 6.5.7m\n")
        with mock.patch.object(drv.rig_state, "mark") as mark:
            drv.verify(rig, self.tmp.name, IC, self.evidence)
        with open(self.evidence) as f:
            text = f.read()
        self.assertIn("6.5.7m", text)
        self.assertIn("uname -a", text)
        mark.assert_called_once()
        self.assertEqual(mark.call_args.args[1], "verify")
        self.assertIn("6.5.7m", mark.call_args.args[2])


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
