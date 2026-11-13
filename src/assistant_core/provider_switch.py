"""Per-step provider switching inside Hermes, for a cheap tier served by another provider.

The ``llm_request`` middleware can only edit a request that is already built for the session's
provider (endpoint, credentials and wire format are fixed by then), so it can rename the model
but not move a step to another provider. Moving a step means swapping the agent's runtime
*before* the request is built, the way Hermes' own fallback chain does mid-turn:

- cheap step:  ``agent.route_binding.bind_route_entry`` with ``{provider, model}`` of the cheap
  tier. Hermes resolves the client and key, picks the wire format, keeps ``messages``;
- strong step: ``agent.agent_runtime_helpers.restore_primary_runtime``, back to the session's
  primary (set the Hermes default model to the strong model). Hermes also calls this at the
  start of every turn, so a turn never starts on the cheap provider.

The swap runs at the start of each loop iteration, right after Hermes' ``prepare_iteration``
(the spot where Hermes itself switches wires, with nothing in flight). Hermes has no plugin hook
there that receives the agent, so ``install`` wraps that one phase function. It is checked
against the pinned Hermes commit (dce1e9b3); ``install`` refuses to run when the phase is not
where it expects it.
"""

import functools
import inspect
import logging


logger = logging.getLogger(__name__)

_HOOK_ATTR = "_assistant_core_step_hook"
_WRAPPED_ATTR = "_assistant_core_wrapped"


class HermesSwitcher:
    """Moves a live Hermes agent between its primary runtime and the cheap provider."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model

    def on_cheap(self, agent) -> bool:
        return str(getattr(agent, "provider", "") or "").lower() == self.provider and bool(
            getattr(agent, "_fallback_activated", False))

    def to_cheap(self, agent) -> None:
        from agent.route_binding import bind_route_entry

        if bind_route_entry(agent, {"provider": self.provider, "model": self.model}, self.provider, self.model) is None:
            raise RuntimeError(f"Hermes has no usable client for provider {self.provider!r}")

    def to_strong(self, agent) -> bool:
        from agent.agent_runtime_helpers import restore_primary_runtime

        return bool(restore_primary_runtime(agent))

    @staticmethod
    def primary(agent) -> tuple:
        """(provider, model) of the session's primary runtime."""
        runtime = getattr(agent, "_primary_runtime", None) or {}
        return (str(runtime.get("provider") or getattr(agent, "provider", "") or ""),
                str(runtime.get("model") or getattr(agent, "model", "") or ""))


def install(step_hook) -> None:
    """Call ``step_hook(agent, messages, api_call_count)`` after every ``prepare_iteration``.

    Idempotent: a second install (plugin reloaded) replaces the hook, never stacks wrappers.
    """
    from agent import conversation_loop

    phase = conversation_loop.prepare_iteration
    if not getattr(phase, _WRAPPED_ATTR, False):
        params = inspect.signature(phase).parameters
        if "messages" not in params or "api_call_count" not in params:
            raise RuntimeError("Hermes prepare_iteration has an unexpected signature; provider switching is off")

        @functools.wraps(phase)
        def wrapper(agent, **kwargs):
            result = phase(agent, **kwargs)
            hook = getattr(conversation_loop, _HOOK_ATTR, None)
            if hook is not None:
                hook(agent, kwargs.get("messages"), kwargs.get("api_call_count"))
            return result

        setattr(wrapper, _WRAPPED_ATTR, True)
        conversation_loop.prepare_iteration = wrapper
    setattr(conversation_loop, _HOOK_ATTR, step_hook)
