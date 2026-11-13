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
    first-strong    strong for the first ASSISTANT_ROUTER_STRONG_FIRST_STEPS steps of a
                    turn, cheap after, never escalated on errors (First-Large baseline)

What a policy sees (``features``) is what happened inside the agent, never
message text. Every feature of step t comes from events finished before step t
(a retry of the step reuses its snapshot), and every one can change from step to
step, so none of them names the task:

    step                    model calls of this turn, this one included
    prev_tool_errors        failed tool calls since the previous model call (a
                            timeout is not counted here), capped at 2
    prev_tool_timeout       1 when one of those tool calls timed out
    prev_tool_duration_log  log1p of the longest of those tool calls, in seconds
    short_exec_streak       consecutive previous steps whose code calls
                            (execute_code, terminal) all ended ok within 3 s, capped at 5
    ctx_growth              sign(d) * log1p(|d|), d = request characters minus those
                            of the first step of the turn
    prev_choice             the previous step's choice (None on the first step)
    elapsed_log, tool_time_cum, cum_tool_failures, error_streak
                            turn-level variants kept for ablations
    prev_tools, last_tool, messages, chars, session_step
                            logged for analysis

The design of this set is in docs/thiet-ke-thuc-nghiem.md (step 4).

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
import math
from threading import RLock
import time

from .settings import RouterSettings, SettingsError


logger = logging.getLogger(__name__)

STRONG, CHEAP = "strong", "cheap"
MAX_SESSIONS = 2000
SOURCE = "assistant-core-router"
EXEC_TOOLS = frozenset({"execute_code", "terminal"})
SHORT_EXEC_S = 3.0          # a code call shorter than this is "short" (sensitivity: 1 / 3 / 5 s)
MAX_PREV_ERRORS, MAX_SHORT_STREAK = 2, 5


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
    if features["prev_tool_errors"] or features.get("prev_tool_timeout"):
        return Decision(STRONG, "tool error in the previous step")
    return Decision(CHEAP, "routine step")


def _first_strong(features, settings):
    # First-Large baseline (Budget-Aware): the first steps on the strong model, the rest cheap,
    # with no escalation after a tool error.
    if features["step"] <= settings.strong_first_steps:
        return Decision(STRONG, "first steps of the turn")
    return Decision(CHEAP, "after the first steps")


POLICIES = {"always-strong": _always_strong, "always-cheap": _always_cheap, "random": _random, "rule": _rule,
            "first-strong": _first_strong}


def register_policy(name: str, policy) -> None:
    """Add a policy: ``policy(features: dict, settings: RouterSettings) -> Decision``."""
    POLICIES[name] = policy


class _Session:
    __slots__ = ("turn_id", "step", "session_step", "last_choice", "tools", "tool_errors", "last_tool",
                 "error_streak", "step_id", "record", "tool_timeouts", "tool_max_s", "exec_calls",
                 "exec_all_short", "short_exec_streak", "first_chars", "turn_started", "tool_time_s",
                 "turn_failures")

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
        self.tool_timeouts = 0   # timed-out tool calls since the previous model call
        self.tool_max_s = 0.0    # longest tool call since the previous model call
        self.exec_calls = 0      # code calls since the previous model call
        self.exec_all_short = True
        self.short_exec_streak = 0
        self.first_chars = None  # request size at the first step of the turn
        self.turn_started = None
        self.tool_time_s = 0.0   # tool time in the turn
        self.turn_failures = 0   # failed or timed-out tool calls in the turn


def _seconds(duration_ms) -> float:
    """A tool call's duration in seconds; 0 when it is missing or not a usable number."""
    try:
        value = float(duration_ms) / 1000.0
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


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
    def __init__(self, settings: RouterSettings, switcher=None):
        if settings.policy not in POLICIES:
            raise SettingsError("ASSISTANT_ROUTER_POLICY cần là một trong: off, " + ", ".join(sorted(POLICIES)) + ".")
        self.settings = settings
        self.policy = POLICIES[settings.policy]
        # Cheap tier on another provider: decide and swap the agent's runtime at the start of
        # each step (before_step); the middleware then only keeps the decision for telemetry.
        self.switcher = switcher
        if switcher is None and settings.cheap_provider:
            from .provider_switch import HermesSwitcher
            self.switcher = HermesSwitcher(settings.cheap_provider, settings.cheap_model)
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

    def tool_result(self, *, session_id=None, task_id=None, tool_name=None, status=None, blocked=False,
                    duration_ms=None, **_):
        """Note a finished (or vetoed) tool call; the next step's features report it."""
        with self._lock:
            state = self._session(session_id or task_id)
            state.tools += 1
            state.last_tool = str(tool_name)[:80] if tool_name else None
            seconds = _seconds(duration_ms)
            state.tool_max_s = max(state.tool_max_s, seconds)
            state.tool_time_s += seconds
            status = None if status is None else str(status).lower()
            ok = not blocked and status in (None, "ok", "success")
            if not blocked and status == "timeout":
                state.tool_timeouts += 1
                state.turn_failures += 1
            elif not ok:
                state.tool_errors += 1
                state.turn_failures += 1
            if tool_name in EXEC_TOOLS:
                state.exec_calls += 1
                state.exec_all_short = state.exec_all_short and ok and seconds < SHORT_EXEC_S

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
        if self.switcher is not None:
            return None  # decided and applied in before_step; the request is already for that provider
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
        now = time.monotonic()
        if turn_id != state.turn_id:
            state.turn_id, state.step = turn_id, 0
            state.first_chars, state.turn_started = None, now
            state.tool_time_s, state.turn_failures, state.short_exec_streak = 0.0, 0, 0
        elif state.step:
            # The previous step of this turn has ended: did its code calls all run short and clean?
            short = state.exec_calls > 0 and state.exec_all_short
            state.short_exec_streak = min(state.short_exec_streak + 1, MAX_SHORT_STREAK) if short else 0
        messages, chars = _size(request)
        if state.step == 0:
            state.first_chars = chars
        growth = None
        if chars is not None and state.first_chars is not None:
            delta = chars - state.first_chars
            growth = round(math.copysign(math.log1p(abs(delta)), delta), 4)
        features = dict(step=state.step + 1, session_step=state.session_step + 1, step_id=step_id,
                        prev_choice=state.last_choice, prev_tools=state.tools,
                        prev_tool_errors=min(state.tool_errors, MAX_PREV_ERRORS),
                        prev_tool_timeout=int(state.tool_timeouts > 0),
                        prev_tool_duration_log=round(math.log1p(state.tool_max_s), 4),
                        short_exec_streak=state.short_exec_streak, ctx_growth=growth,
                        last_tool=state.last_tool if state.tools else None,
                        error_streak=state.error_streak + 1 if state.tool_errors else 0,
                        elapsed_log=round(math.log1p(max(0.0, now - state.turn_started)), 4),
                        tool_time_cum=round(math.log1p(state.tool_time_s), 4),
                        cum_tool_failures=state.turn_failures,
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
        state.tools = state.tool_errors = state.tool_timeouts = state.exec_calls = 0
        state.tool_max_s, state.exec_all_short = 0.0, True
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

    # ---- provider switching (cheap tier on another provider) -------------

    def before_step(self, agent, messages, api_call_count):
        """Start of a Hermes loop iteration: decide this step and put the agent on that tier."""
        try:
            self._before_step(agent, messages, api_call_count)
        except Exception:
            self._failures += 1
            if self._failures <= 3:
                logger.warning("assistant-core router failed; the step runs on the primary model", exc_info=True)
            try:  # never leave a failed step on a half-switched or cheap runtime
                if self.switcher.on_cheap(agent):
                    self.switcher.to_strong(agent)
            except Exception:
                logger.warning("assistant-core router could not restore the primary runtime", exc_info=True)

    def _before_step(self, agent, messages, api_call_count):
        settings, switcher = self.settings, self.switcher
        primary_provider, primary_model = switcher.primary(agent)
        platform = str(getattr(agent, "platform", "") or "")
        if settings.platforms and platform not in settings.platforms:
            return
        if settings.provider and primary_provider != settings.provider:
            return
        session_id = str(getattr(agent, "session_id", "") or "")
        turn_id = str(getattr(agent, "_current_turn_id", "") or "")
        # Same id Hermes gives this model call (api_request_id), so telemetry finds the decision.
        step_id = f"{turn_id}:api:{api_call_count}"
        with self._lock:
            state = self._session(session_id)
            if state.step_id == step_id and state.record is not None:
                record = state.record  # the iteration was re-issued; keep its decision
            else:
                record = self._decide(state, {"model": primary_model, "messages": messages}, turn_id, step_id)
            on_cheap = switcher.on_cheap(agent)
            if record["choice"] == CHEAP and not on_cheap:
                switcher.to_cheap(agent)
            elif record["choice"] == STRONG and on_cheap and not switcher.to_strong(agent):
                # Hermes keeps a fallback while the primary is in a rate-limit cooldown.
                record["reason"] = (record["reason"] + "; primary unavailable, stayed cheap")[:120]
            record["model"] = str(getattr(agent, "model", "") or "")
            record["provider"] = str(getattr(agent, "provider", "") or "")

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
