#!/usr/bin/env python3
"""Unit tests for the pure helpers in install-driver.py.

The install itself is an integration test against a live emulator; these cover
the one piece of logic that is easy to get subtly wrong and expensive to debug
mid-install: matching inst's disc requests against changer filenames. The
requests are the exact strings observed in the 6.5.7 install transcript.
"""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "install_driver", Path(__file__).with_name("install-driver.py")
)
drv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drv)

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
