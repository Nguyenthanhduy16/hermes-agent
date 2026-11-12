"""Choose the model for each step of the agent loop (the SCER-AH router framework).

Hermes runs one model for a whole session. This module is called through the
``llm_request`` middleware right before every model call of the agent loop and
may point that single call at another model of the same provider:

    state of the step -> policy -> "strong" | "cheap" -> request["model"]

It is the plumbing, not the research method: the bundled policies are the
baselines every experiment needs, and a new policy plugs in with
``register_policy``.

    always-strong   every step on the strong model (quality ceiling)
    always-cheap    every step on the cheap model (cost floor)
    random          strong with probability ASSISTANT_ROUTER_STRONG_SHARE at each
                    step; the draw depends only on the seed and the step id
    rule            strong for the first step(s) of a turn and for the step right
                    after a failed tool call, cheap otherwise

What a policy sees (``features``) is what happened inside the agent, never
message text: the step number, the tool calls made since the previous model
call and whether any failed, the previous choice, and the request size.

Limits that come from Hermes (checked on commit 93c9360a):

- one model call per step: a step cannot try the cheap model and then fall
  back to the strong one; escalation happens at the next step;
- only the model name (and the optional per-tier request fields) changes, so
  both models must be served by the provider and protocol of the session;
- a retry of the same step keeps its decision.

Any failure in here leaves the request untouched, so the step runs on the
Hermes default model. Set that default to the strong model.
"""

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import logging
from threading import RLock

from .settings import RouterSettings, SettingsError


logger = logging.getLogger(__name__)

STRONG, CHEAP = "strong", "cheap"
MAX_SESSIONS = 2000
SOURCE = "assistant-core-router"


@dataclass(frozen=True)
class Decision:
    choice: str                 # STRONG or CHEAP
    reason: str                 # short, fixed wording; never message text
    score: float | None = None  # the policy's own number, if it has one


def _always_strong(features, settings):
    return Decision(STRONG, "policy always-strong")


def _always_cheap(features, settings):
    return Decision(CHEAP, "policy always-cheap")


def _unit(text: str) -> float:
    """A stable number in [0, 1) from text (independent of PYTHONHASHSEED)."""
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big") / 2 ** 64


def _random(features, settings):
    draw = _unit(f"{settings.seed}:{features['step_id']}")
    return Decision(STRONG if draw < settings.strong_share else CHEAP, "random draw", round(draw, 6))


def _rule(features, settings):
    if features["step"] <= settings.strong_first_steps:
        return Decision(STRONG, "first step of the turn")
    if features["prev_tool_errors"]:
        return Decision(STRONG, "tool error in the previous step")
    return Decision(CHEAP, "routine step")


POLICIES = {"always-strong": _always_strong, "always-cheap": _always_cheap, "random": _random, "rule": _rule}


def register_policy(name: str, policy) -> None:
    """Add a policy: ``policy(features: dict, settings: RouterSettings) -> Decision``."""
    POLICIES[name] = policy


class _Session:
    __slots__ = ("turn_id", "step", "session_step", "last_choice", "tools", "tool_errors", "last_tool",
                 "error_streak", "step_id", "record")

    def __init__(self):
        self.turn_id = None
        self.step = 0            # model calls routed in the current turn
        self.session_step = 0
        self.last_choice = None
        self.tools = 0           # tool calls since the previous model call
        self.tool_errors = 0
        self.last_tool = None
        self.error_streak = 0    # consecutive steps that followed a tool error
        self.step_id = None      # the step the stored record belongs to
        self.record = None


def _size(request: dict):
    """(message count, characters) of the conversation in a provider request, when it can be told."""
    items = request.get("messages")
    if not isinstance(items, list):
        items = request.get("input")
    if not isinstance(items, list):
        return None, None
    try:
        return len(items), len(json.dumps(items, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(items), None


class Router:
    def __init__(self, settings: RouterSettings):
        if settings.policy not in POLICIES:
            raise SettingsError("ASSISTANT_ROUTER_POLICY cần là một trong: off, " + ", ".join(sorted(POLICIES)) + ".")
        self.settings = settings
        self.policy = POLICIES[settings.policy]
        self._models = {STRONG: settings.strong_model, CHEAP: settings.cheap_model}
        self._overrides = {STRONG: settings.strong_request, CHEAP: settings.cheap_request}
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._lock = RLock()
        self._failures = 0

    # ---- observations ---------------------------------------------------

    def _session(self, session_id) -> _Session:
        key = str(session_id or "")
        state = self._sessions.get(key)
        if state is None:
            state = self._sessions[key] = _Session()
            if len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        else:
            self._sessions.move_to_end(key)
        return state

    def tool_result(self, *, session_id=None, task_id=None, tool_name=None, status=None, blocked=False, **_):
        """Note a finished (or vetoed) tool call; the next step's features report it."""
        with self._lock:
            state = self._session(session_id or task_id)
            state.tools += 1
            state.last_tool = str(tool_name)[:80] if tool_name else None
            if blocked or (status is not None and str(status) not in ("ok", "success")):
                state.tool_errors += 1

    # ---- the middleware -------------------------------------------------

    def llm_request(self, request=None, **context):
        """``llm_request`` middleware: return ``{"request": ...}`` to reroute this model call, else None."""
        try:
            return self._route(request, context)
        except Exception:
            self._failures += 1
            if self._failures <= 3:  # the same fault would repeat at every step
                logger.warning("assistant-core router failed; the step runs on the default model", exc_info=True)
            return None

    def _route(self, request, context):
        settings = self.settings
        if not isinstance(request, dict) or not isinstance(request.get("model"), str):
            return None
        platform = str(context.get("platform") or "")
        if settings.platforms and platform not in settings.platforms:
            return None
        if settings.provider and str(context.get("provider") or "") != settings.provider:
            return None
        session_id = context.get("session_id") or context.get("task_id") or ""
        turn_id = str(context.get("turn_id") or "")
        step_id = str(context.get("api_request_id") or f"{session_id}:{turn_id}:{context.get('api_call_count')}")
        with self._lock:
            state = self._session(session_id)
            if state.step_id == step_id and state.record is not None:
                record = state.record  # a retry of the same step keeps its decision
            else:
                record = self._decide(state, request, turn_id, step_id)
        return self._apply(request, record)

    def _decide(self, state: _Session, request: dict, turn_id: str, step_id: str) -> dict:
        if turn_id != state.turn_id:
            state.turn_id, state.step = turn_id, 0
        messages, chars = _size(request)
        features = dict(step=state.step + 1, session_step=state.session_step + 1, step_id=step_id,
                        prev_choice=state.last_choice, prev_tools=state.tools,
                        prev_tool_errors=state.tool_errors, last_tool=state.last_tool if state.tools else None,
                        error_streak=state.error_streak + 1 if state.tool_errors else 0,
                        messages=messages, chars=chars)
        decision = self.policy(features, self.settings)
        if not isinstance(decision, Decision) or decision.choice not in self._models:
            raise ValueError("a policy must return a Decision of 'strong' or 'cheap'")
        default_model = request["model"]
        model = self._models[decision.choice]
        record = dict(
            policy=self.settings.policy, choice=decision.choice, reason=str(decision.reason)[:120],
            score=float(decision.score) if isinstance(decision.score, (int, float)) else None,
            model=model, default_model=default_model,
            switched=state.last_choice is not None and state.last_choice != decision.choice,
            features={key: value for key, value in features.items() if key != "step_id"})
        state.step += 1
        state.session_step += 1
        state.error_streak = features["error_streak"]
        state.last_choice = decision.choice
        state.tools = state.tool_errors = 0
        state.last_tool = None
        state.step_id, state.record = step_id, record
        return record

    def _apply(self, request: dict, record: dict):
        overrides = self._overrides[record["choice"]]
        if request["model"] == record["model"] and not overrides:
            return None  # nothing to change; the decision is still recorded
        routed = dict(request, model=record["model"])
        for key, value in overrides:
            if value is None:
                routed.pop(key, None)
            else:
                routed[key] = value
        return {"request": routed, "source": SOURCE, "name": record["policy"],
                "reason": f"{record['choice']}: {record['reason']}"}

    # ---- telemetry ------------------------------------------------------

    def decision_for(self, session_id=None, api_request_id=None, task_id=None, **_) -> dict | None:
        """The decision taken for a step, for the telemetry row of that model call."""
        if not api_request_id:
            return None
        with self._lock:
            state = self._sessions.get(str(session_id or task_id or ""))
            if state is None or state.step_id != str(api_request_id):
                return None
            return state.record
