"""Pluggable Agent Console sections.

A feature module (listed in ``EXTENSION_MODULES``) calls ``register(Section(...))``
when imported. ``admin_web`` then routes:

- ``GET  /api/assistant/<name>``         -> ``section.snapshot(ctx, query)``
- ``POST /api/assistant/<name>/action``  -> ``section.action(ctx, data)``

The web layer has already enforced the session, same-origin, CSRF and the
system-owner role before a section runs, re-checks the role afterwards, and
writes an audit event for every action (section and action id only). Sections
never receive the session, cookies or account data.

Action payloads use the console's descriptor contract::

    {"action": str, "expected_revision": str|int, "confirm": true, "values": {...}}

``validate_action`` checks that envelope; sections validate ``values`` themselves
and raise ``AdminError`` with a user-facing message on any problem. A successful
action returns ``{"ok": True, "message": "..."}``.

Snapshots are plain JSON dicts with at least ``section``, ``available`` and
``items``; they may include ``actions`` (descriptors rendered by the console),
``message`` and ``updated_at``. In ``demo`` mode a section must return synthetic
data and never read the real Hermes home or contact any service.
"""
from dataclasses import dataclass, field
import importlib
import logging
import os
from pathlib import Path
import re
from typing import Callable, Mapping, Optional

from .admin_auth import AdminError

logger = logging.getLogger(__name__)

# Feature modules loaded on first use, in this package. A missing module is
# skipped so features can be developed and shipped independently.
EXTENSION_MODULES = ('admin_observe', 'admin_bench', 'admin_integrations', 'admin_operations', 'admin_briefing')

# Names already served by built-in handlers; extensions may not shadow them.
RESERVED = frozenset((
    'overview', 'runtime', 'skills', 'routines', 'usage', 'users', 'sessions', 'history', 'files', 'kanban', 'chat',
    'models', 'logs', 'plugins', 'mcp', 'channels', 'webhooks', 'pairing', 'profiles', 'config', 'keys',
    'documentation', 'memories', 'access',
))
NAME = re.compile(r'[a-z][a-z0-9-]{1,31}')
QUERY_VALUE_LIMIT = 200


@dataclass(frozen=True)
class ConsoleContext:
    """What a section may know about the deployment. Paths are explicit, never guessed."""
    home: Path                 # the selected Hermes home (HERMES_HOME)
    mode: str                  # 'local' or 'demo'
    assistant_dir: Path        # assistant-core data dir: telemetry.sqlite3, quota.sqlite3
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @property
    def demo(self) -> bool:
        return self.mode == 'demo'


@dataclass(frozen=True)
class Section:
    name: str
    snapshot: Callable[[ConsoleContext, dict], dict]
    action: Optional[Callable[[ConsoleContext, dict], dict]] = None
    query_keys: frozenset = frozenset()


_registry: dict = {}
_loaded = False


def register(section: Section) -> Section:
    if not isinstance(section, Section) or not NAME.fullmatch(section.name or ''):
        raise ValueError('Console sections need a short lowercase name.')
    if section.name in RESERVED:
        raise ValueError('Console section name is reserved: ' + section.name)
    if not all(isinstance(key, str) and NAME.fullmatch(key.replace('_', '-')) for key in section.query_keys):
        raise ValueError('Invalid query key for console section ' + section.name)
    _registry[section.name] = section
    return section


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    for module in EXTENSION_MODULES:
        qualified = f'{__package__}.{module}'
        try:
            importlib.import_module(qualified)
        except ModuleNotFoundError as error:
            if error.name != qualified:
                logger.exception('Console extension %s failed to import', module)
        except Exception:
            # One broken feature must not take the whole console down.
            logger.exception('Console extension %s failed to import', module)


def get(name: str) -> Optional[Section]:
    _load()
    return _registry.get(name)


def names() -> list:
    _load()
    return sorted(_registry)


def clean_query(section: Section, query: dict) -> dict:
    unknown = set(query) - set(section.query_keys)
    if unknown:
        raise AdminError('Unsupported filter for this page.')
    for value in query.values():
        if not isinstance(value, str) or len(value) > QUERY_VALUE_LIMIT or '\0' in value:
            raise AdminError('Filter values must be short text.')
    return dict(query)


def validate_action(data) -> tuple:
    """Check the confirmed-descriptor envelope; return (action_id, revision, values)."""
    if (not isinstance(data, dict) or set(data) != {'action', 'expected_revision', 'confirm', 'values'}
            or data.get('confirm') is not True):
        raise AdminError('Confirm an exact management action before submitting.')
    action, revision, values = data['action'], data['expected_revision'], data['values']
    if not isinstance(action, str) or not action or len(action) > 80 or not isinstance(values, dict):
        raise AdminError('Invalid management action.')
    if isinstance(revision, bool) or not isinstance(revision, (str, int)):
        raise AdminError('Invalid management action revision.')
    return action, revision, values
