"""Opt-in: the provider switch against installed, unmodified Hermes.

Checks that the Hermes functions the switch relies on exist with the expected shape at the
pinned commit, and that the wrapped loop phase still reaches Hermes' own ``prepare_iteration``
through ``_run_phase``. No network, model or messaging platform is used; the live check is a
real routed run (docs/dinh-tuyen.md).
"""

import inspect
import os
from pathlib import Path
import sys
import unittest

SRC = str(Path(__file__).parents[1] / "src")


@unittest.skipUnless(os.getenv("HERMES_INTEGRATION_TESTS") == "1", "Requires installed Hermes runtime")
class RealHermesProviderSwitchTests(unittest.TestCase):
    def setUp(self):
        from agent import conversation_loop

        self.loop = conversation_loop
        self.original = conversation_loop.prepare_iteration
        self.addCleanup(self.restore)

    def restore(self):
        self.loop.prepare_iteration = self.original
        if hasattr(self.loop, "_assistant_core_step_hook"):
            del self.loop._assistant_core_step_hook

    def test_hermes_runtime_functions_have_the_expected_shape(self):
        from agent.agent_runtime_helpers import restore_primary_runtime
        from agent.route_binding import bind_route_entry

        self.assertEqual(["agent", "entry", "provider", "model"], list(inspect.signature(bind_route_entry).parameters))
        self.assertEqual(["agent"], list(inspect.signature(restore_primary_runtime).parameters))
        params = inspect.signature(self.original).parameters
        self.assertIn("messages", params)
        self.assertIn("api_call_count", params)

    def test_the_wrapped_phase_runs_through_run_phase(self):
        if SRC not in sys.path:
            sys.path.insert(0, SRC)
            self.addCleanup(sys.path.remove, SRC)
        from agent.turn_iteration_prep import IterationPrep
        from assistant_core import provider_switch

        calls = []

        def fake_phase(agent, *, messages, api_call_count, user_message=None, current_turn_user_idx=None):
            calls.append(("phase", api_call_count))
            return IterationPrep("fallthrough", messages, None, current_turn_user_idx)

        # Wrap a stand-in with the real phase's signature, then let Hermes' _run_phase call it.
        self.loop.prepare_iteration = fake_phase
        provider_switch.install(lambda agent, messages, count: calls.append(("hook", count, len(messages))))
        wrapped = self.loop.prepare_iteration
        self.assertTrue(getattr(wrapped, "_assistant_core_wrapped", False))
        self.assertEqual(list(inspect.signature(fake_phase).parameters),
                         list(inspect.signature(wrapped).parameters))

        class State:
            messages = [{"role": "user", "content": "hi"}]
            api_call_count = 3
            user_message = "hi"
            current_turn_user_idx = 0
            request_logger = None

        self.assertEqual("fallthrough", self.loop._run_phase(wrapped, object(), State()).action)
        self.assertEqual([("phase", 3), ("hook", 3, 1)], calls)
