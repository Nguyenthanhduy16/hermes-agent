"""Opt-in: the bench predicts the session id the installed Hermes derives for a task.

A task that times out never receives the X-Hermes-Session-Id header, so the bench records the
id it expects (bench.expected_session_id); this checks that guess against Hermes itself.
"""

import os
from pathlib import Path
import sys
import unittest

SRC = str(Path(__file__).parents[1] / "src")


@unittest.skipUnless(os.getenv("HERMES_INTEGRATION_TESTS") == "1", "Requires installed Hermes runtime")
class RealHermesBenchSessionTests(unittest.TestCase):
    def test_expected_session_id_matches_hermes(self):
        from gateway.platforms.api_server import _derive_chat_session_id

        if SRC not in sys.path:
            sys.path.insert(0, SRC)
        from assistant_core import bench

        for system, prompt in [("Benchmark run 20261112-123231-c41e00, task dc-x, attempt 1.", "Do it.\nNow."),
                               ("", "chỉ một câu")]:
            self.assertEqual(_derive_chat_session_id(system, prompt), bench.expected_session_id(system, prompt))


if __name__ == "__main__":
    unittest.main()
