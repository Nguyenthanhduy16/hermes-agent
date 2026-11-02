"""System & operations console section ("System"): status, host resources,
opt-in gateway control, health checks, Hermes-home backups and update checks.

Safety rules:

- Owner-only (enforced by the web route). Nothing from the browser becomes a
  command, path or URL: actions pick from fixed descriptors; backup names are
  matched against a strict pattern inside a fixed folder.
- Gateway control is off unless ``HERMES_CONSOLE_GATEWAY_CONTROL`` names one
  service, e.g. ``systemd-user:hermes-gateway.service`` or
  ``docker:hermes-zalo-bot``. Commands are fixed argv lists (no shell) with a
  minimal environment and timeouts; ``stop`` needs the service name typed.
- Status detection matches Hermes itself: ``<home>/gateway.pid`` (PID +
  start-time fingerprint) and ``<home>/gateway_state.json``; with control
  configured, ``systemctl show`` / ``docker inspect`` are authoritative.
- Backups are private tar.gz archives under ``<home>/backups/console`` (0700
  folder, 0600 files). Symlinks, sockets, caches, runtimes, oversized logs and
  the backups folder itself are never archived; SQLite files are snapshotted
  through the SQLite backup API. Secrets (``.env``, ``auth.json``) are left out
  unless the owner ticks a box. Restore is a documented server command only.
- Demo mode returns synthetic data and simulates actions in memory: no
  subprocess, disk or network access.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import threading
import time
from urllib.parse import quote, urlsplit
import urllib.error
import urllib.request

import yaml

from .admin_assistant import REPO_ROOT, _git_head, redact
from .admin_auth import AdminError
from .console_sections import Section, register, validate_action

CONTROL_ENV = 'HERMES_CONSOLE_GATEWAY_CONTROL'
CONTROL_KINDS = ('systemd-user', 'systemd', 'systemd-sudo', 'docker')
VERBS = ('start', 'stop', 'restart')
_UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,99}')
_CONTAINER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
_GIT_REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}')
_SHA = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
CONTROL_TIMEOUT = 120          # docker stop waits up to 60 s for a graceful drain
STATUS_TIMEOUT = 5
GIT_TIMEOUT = 15
HEALTH_TIMEOUT = 2
STATUS_CACHE_SECONDS = 5
HEALTH_CACHE_SECONDS = 10
STATE_STALE_SECONDS = 120      # Hermes re-stamps gateway_state.json every 60 s
HEARTBEAT_FRESH_SECONDS = 180  # same rule as admin_automation
SAMPLE_SECONDS = 60
HISTORY_POINTS = 24 * 60       # ~24 h of one-minute samples, memory only
CHART_BUCKETS = 48             # 30-minute bars
SMALL_FILE = 1024 * 1024
OUTPUT_LIMIT = 4096
DEEP_CHECKS_SECONDS = 15 * 60  # a "Run checks" result is shown this long

BACKUP_PATTERN = re.compile(r'hermes-home-(\d{8}-\d{6})(-secrets)?\.tar\.gz')
BACKUP_KEEP_DEFAULT = 5
BACKUP_KEEP_MAX = 50
BACKUP_MAX_INPUT_BYTES = 4 * 1024 ** 3
BACKUP_MAX_FILES = 200000
BACKUP_MAX_SECONDS = 30 * 60
BACKUP_MIN_FREE_BYTES = 512 * 1024 ** 2
BACKUP_LOG_CAP = 5 * 1024 ** 2
MEMORY_READ_LIMIT = 8 * 1024 ** 2
MANIFEST = 'MANIFEST.json'
ARCHIVE_ROOT = 'hermes-home'
# Regenerable or dangerous trees, matched at the home root only.
ROOT_EXCLUDED_DIRS = frozenset({
    'backups', 'state-snapshots', 'checkpoints', 'installs', 'tools', 'cache', 'audio_cache', 'image_cache',
    'hermes-agent', 'browser_profiles', 'logs-archive'})
# Excluded wherever they appear.
EXCLUDED_DIRS = frozenset({
    '__pycache__', '.git', 'node_modules', '.venv', 'venv', 'site-packages', '.cache', '.tox', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'browser-profiles', 'browser-profile'})
EXCLUDED_NAMES = frozenset({'gateway.pid', 'cron.pid', 'gateway.lock', '.backup.lock'})
EXCLUDED_SUFFIXES = ('.pyc', '.pyo', '.lock', '.sock', '.partial')
SQLITE_SIDECARS = ('-wal', '-shm', '-journal')
SECRET_NAMES = frozenset({'.env', 'auth.json'})
SQLITE_MAGIC = b'SQLite format 3\x00'
CHANNEL_KEYS = {'telegram': 'TELEGRAM_BOT_TOKEN', 'zalo': 'ZALO_BOT_TOKEN', 'discord': 'DISCORD_BOT_TOKEN',
                'slack': 'SLACK_BOT_TOKEN'}
FORWARDED_ENV = ('PATH', 'HOME', 'USER', 'LOGNAME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'DOCKER_HOST',
                 'DOCKER_CONTEXT', 'DOCKER_CERT_PATH', 'DOCKER_TLS_VERIFY')
DEFAULT_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
_ENV_LINE = re.compile(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)')


def _now():
    return time.time()


def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec='seconds') if stamp else None


# ---- process runner (patched in tests; never a shell) ----------------------

def _which(name, environ):
    return shutil.which(name, path=environ.get('PATH') or DEFAULT_PATH)


def _child_env(environ):
    env = {key: environ[key] for key in FORWARDED_ENV if environ.get(key)}
    env.setdefault('PATH', DEFAULT_PATH)
    env.update(LANG='C.UTF-8', LC_ALL='C.UTF-8', GIT_TERMINAL_PROMPT='0', SYSTEMD_PAGER='', PAGER='cat')
    return env


def _run(argv, timeout, environ):
    """Run a fixed argv list. Returns (returncode, stdout, stderr); never raises for the child's failure."""
    if not argv or not all(isinstance(part, str) and part and '\0' not in part for part in argv):
        raise AdminError('Invalid command.')
    try:
        done = subprocess.run(argv, shell=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                              env=_child_env(environ), check=False, close_fds=True)
    except subprocess.TimeoutExpired:
        return 124, '', 'The command timed out.'
    except OSError:
        return 127, '', 'The command could not be started.'
    decode = lambda raw: (raw or b'')[:OUTPUT_LIMIT].decode('utf-8', 'replace')
    return done.returncode, decode(done.stdout), decode(done.stderr)


RUNNER = _run


def _http_get(url, timeout):
    """GET without proxies or redirects; returns (status, None) or (None, reason)."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, headers={'Accept': 'application/json'}), timeout=timeout) as reply:
            reply.read(2048)
            return reply.status, None
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, 'unreachable'


HTTP_GET = _http_get
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _cached(key, ttl, compute):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = compute()
    with _CACHE_LOCK:
        _CACHE[key] = (now + ttl, value)
    return value


def _forget(prefix):
    with _CACHE_LOCK:
        for key in [k for k in _CACHE if k[0] == prefix]:
            _CACHE.pop(key, None)


# ---- small safe readers -----------------------------------------------------

def _read_small(path, limit=256 * 1024):
    """Bytes of a regular, non-symlink file, or None."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            return handle.read(limit + 1)[:limit]
    except OSError:
        return None
    finally:
        os.close(fd)


def _read_json(path, limit=256 * 1024):
    raw = _read_small(path, limit)
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode('utf-8-sig').strip() or 'null')
    except (ValueError, UnicodeDecodeError):
        return None
    return {'pid': value} if isinstance(value, int) and not isinstance(value, bool) else value


def _config(home):
    raw = _read_small(Path(home) / 'config.yaml', 1024 * 1024)
    if raw is None:
        return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return False
    return data if isinstance(data, dict) else False


def _env_files(home):
    return [('Hermes home', Path(home) / '.env'), ('project', REPO_ROOT / '.env')]


def _env_keys(path):
    """Names (never values) of non-empty variables in an env file."""
    raw = _read_small(path)
    keys = set()
    if raw is None:
        return keys
    for line in raw.decode('utf-8', 'replace').splitlines():
        match = _ENV_LINE.fullmatch(line)
        if match and match.group(2).strip().strip('"\'').strip():
            keys.add(match.group(1))
    return keys


def _configured(name, home, environ):
    if environ.get(name, '').strip():
        return True
    return any(name in _env_keys(path) for _, path in _env_files(home))


# ---- host resources and history ---------------------------------------------

def _meminfo():
    try:
        info = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, value = line.partition(':')
            parts = value.split()
            if parts and parts[0].isdigit():
                info[key] = int(parts[0]) * 1024
        return info
    except OSError:
        return {}


def _host_uptime():
    try:
        return float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _percent(used, total):
    return round(used / total * 100, 1) if isinstance(used, (int, float)) and total else None


def read_resources(home):
    result = {'cpu_count': os.cpu_count(), 'load_1m': None, 'load_5m': None, 'load_15m': None,
              'memory_total_bytes': None, 'memory_used_bytes': None, 'memory_available_bytes': None,
              'swap_total_bytes': None, 'swap_used_bytes': None, 'disk_total_bytes': None, 'disk_used_bytes': None,
              'disk_free_bytes': None, 'host_uptime_seconds': None}
    try:
        result['load_1m'], result['load_5m'], result['load_15m'] = (round(v, 2) for v in os.getloadavg())
    except (OSError, AttributeError):
        pass
    info = _meminfo()
    if 'MemTotal' in info and 'MemAvailable' in info:
        result.update(memory_total_bytes=info['MemTotal'], memory_available_bytes=info['MemAvailable'],
                      memory_used_bytes=info['MemTotal'] - info['MemAvailable'])
    if 'SwapTotal' in info and 'SwapFree' in info:
        result.update(swap_total_bytes=info['SwapTotal'], swap_used_bytes=info['SwapTotal'] - info['SwapFree'])
    try:
        disk = shutil.disk_usage(home if Path(home).is_dir() else REPO_ROOT)
        result.update(disk_total_bytes=disk.total, disk_used_bytes=disk.used, disk_free_bytes=disk.free)
    except OSError:
        pass
    uptime = _host_uptime()
    result['host_uptime_seconds'] = round(uptime) if uptime is not None else None
    result['memory_percent'] = _percent(result['memory_used_bytes'], result['memory_total_bytes'])
    result['disk_percent'] = _percent(result['disk_used_bytes'], result['disk_total_bytes'])
    result['swap_percent'] = _percent(result['swap_used_bytes'], result['swap_total_bytes'])
    return result


def pressure(resources):
    alerts = []
    if (resources.get('memory_percent') or 0) > 90:
        alerts.append({'id': 'memory', 'label': 'Memory is almost full',
                       'detail': f"{resources['memory_percent']}% in use. The assistant may slow down or be stopped."})
    if (resources.get('disk_percent') or 0) > 90:
        alerts.append({'id': 'disk', 'label': 'Disk is almost full',
                       'detail': f"{resources['disk_percent']}% in use. Free space before backups or logs fill it."})
    cpus, load = resources.get('cpu_count'), resources.get('load_1m')
    if cpus and load is not None and load > cpus:
        alerts.append({'id': 'load', 'label': 'The server is overloaded',
                       'detail': f'Load {load} is above the {cpus} available CPU(s).'})
    return alerts


class History:
    """In-process ring buffer of one-minute samples; started lazily, memory only."""

    def __init__(self):
        self.lock = threading.Lock()
        self.points = deque(maxlen=HISTORY_POINTS)
        self.thread = None
        self.enabled = True

    def record(self, stamp, resources):
        with self.lock:
            self.points.append({'t': stamp, 'load_1m': resources.get('load_1m'),
                                'memory_percent': resources.get('memory_percent'),
                                'disk_percent': resources.get('disk_percent')})

    def ensure(self, home):
        with self.lock:
            if self.thread is not None or not self.enabled:
                return
            self.thread = threading.Thread(target=self._loop, args=(Path(home),), name='console-ops-sampler',
                                           daemon=True)
        self.thread.start()

    def _loop(self, home):
        while True:
            try:
                self.record(_now(), read_resources(home))
            except Exception:  # sampling must never take the console down
                pass
            time.sleep(SAMPLE_SECONDS)

    def series(self, now=None, buckets=CHART_BUCKETS, span=24 * 3600):
        now = now or _now()
        width = span / buckets
        with self.lock:
            points = list(self.points)
        rows = []
        for index in range(buckets):
            start = now - span + index * width
            inside = [p for p in points if start <= p['t'] < start + width]
            if not inside:
                continue
            average = lambda key: (round(sum(p[key] for p in inside if p[key] is not None)
                                         / max(1, sum(p[key] is not None for p in inside)), 2)
                                   if any(p[key] is not None for p in inside) else None)
            peak = [p['load_1m'] for p in inside if p['load_1m'] is not None]
            rows.append({'t': _iso(start + width), 'samples': len(inside), 'load_1m': average('load_1m'),
                         'load_peak': max(peak) if peak else None, 'memory_percent': average('memory_percent'),
                         'disk_percent': average('disk_percent')})
        return {'sample_seconds': SAMPLE_SECONDS, 'bucket_seconds': int(width), 'samples': len(points),
                'points': rows}


HISTORY = History()


# ---- gateway status ---------------------------------------------------------

@dataclass(frozen=True)
class Control:
    kind: str
    target: str

    def command(self, verb, which=lambda name: name):
        """Fixed argv for verb; ``which`` maps an executable name to its absolute path."""
        if verb not in VERBS:
            raise AdminError('Unsupported gateway action.')
        if self.kind == 'docker':
            return [which('docker'), verb, *(['-t', '60'] if verb != 'start' else []), self.target]
        systemctl = which('systemctl')
        if self.kind == 'systemd-sudo':
            prefix = [which('sudo'), '-n', systemctl]
        else:
            prefix = [systemctl, '--user'] if self.kind == 'systemd-user' else [systemctl]
        return prefix + ['--no-block', verb, self.target]

    def status_command(self, which=lambda name: name):
        if self.kind == 'docker':
            return [which('docker'), 'inspect', '--type', 'container', '--format', '{{json .State}}', self.target]
        user = ['--user'] if self.kind == 'systemd-user' else []
        return [which('systemctl'), *user, 'show', '--property=LoadState,ActiveState,SubState,MainPID,'
                'ActiveEnterTimestampMonotonic', self.target]

    def display(self, verb):
        """Plain CLI equivalent for the owner (no absolute paths)."""
        return ' '.join(self.command(verb)).replace(' --no-block', '')


def parse_control(raw):
    """(Control | None, error | None) from HERMES_CONSOLE_GATEWAY_CONTROL."""
    value = (raw or '').strip()
    if not value or value.lower() in ('off', 'false', 'none', '0'):
        return None, None
    kind, sep, target = value.partition(':')
    if not sep or kind not in CONTROL_KINDS:
        return None, f'{CONTROL_ENV} must start with one of: ' + ', '.join(k + ':' for k in CONTROL_KINDS)
    if kind == 'docker':
        if not _CONTAINER.fullmatch(target):
            return None, 'The container name may only use letters, digits, ".", "_" and "-".'
    elif not _UNIT.fullmatch(target) or ('.' in target and not target.endswith('.service')) or '..' in target:
        return None, 'The unit must be a plain systemd service name such as hermes-gateway.service.'
    return Control(kind, target), None


def _proc_start(pid):
    try:
        return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid):
    if Path('/proc').is_dir() and Path('/proc/self').exists():
        return Path(f'/proc/{pid}').exists()
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_cmdline(pid):
    raw = _read_small(f'/proc/{pid}/cmdline', 64 * 1024)
    return raw.replace(b'\0', b' ').decode('utf-8', 'replace') if raw else ''


def _process_uptime(pid):
    start, uptime = _proc_start(pid), _host_uptime()
    try:
        ticks = os.sysconf('SC_CLK_TCK')
    except (ValueError, OSError, AttributeError):
        return None
    if start is None or uptime is None:
        return None
    return max(0, round(uptime - start / ticks))


def _parse_updated(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _pid_file_status(home):
    record = _read_json(Path(home) / 'gateway.pid')
    runtime = _read_json(Path(home) / 'gateway_state.json')
    runtime = runtime if isinstance(runtime, dict) else {}
    pid = record.get('pid') if isinstance(record, dict) else None
    pid = pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None
    result = {'state': 'stopped', 'pid': None, 'uptime_seconds': None, 'detected_by': 'pid file', 'detail': None}
    if pid is None:
        retained = runtime.get('gateway_state')
        if runtime.get('desired_state') != 'stopped' and retained in ('startup_failed', 'degraded'):
            result.update(state='failed', detail='The last start failed; see the gateway log.')
        elif not runtime and record is None:
            result.update(state='unknown', detail='No gateway status files in this Hermes home yet.')
        return result, runtime
    if not _pid_alive(pid):
        result['detail'] = 'The recorded process is no longer running.'
        return result, runtime
    recorded = record.get('start_time') if isinstance(record, dict) else None
    current = _proc_start(pid)
    if isinstance(recorded, int) and current is not None and recorded != current:
        result['detail'] = 'The recorded process ID now belongs to another program.'
        return result, runtime
    if not isinstance(recorded, int) and 'gateway' not in _pid_cmdline(pid):
        result.update(state='unknown', detail='A process with the recorded ID exists but could not be confirmed.')
        return result, runtime
    result.update(state='running', pid=pid, uptime_seconds=_process_uptime(pid))
    updated = _parse_updated(runtime.get('updated_at'))
    if runtime and (updated is None or _now() - updated > STATE_STALE_SECONDS):
        result['detail'] = 'The process is alive but has not refreshed its status for over 2 minutes.'
        result['stale'] = True
    elif runtime.get('gateway_state') in ('starting', 'draining', 'degraded'):
        result['detail'] = 'Gateway reports: ' + str(redact(runtime.get('gateway_state'), 30))
    return result, runtime


def _service_status(control, environ):
    """Ask systemd/docker directly (cached briefly). Returns a partial status dict or None."""
    def compute():
        tool = 'docker' if control.kind == 'docker' else 'systemctl'
        path = _which(tool, environ)
        if not path:
            return {'state': 'unknown', 'detail': f'{tool} is not installed or not on PATH for the console.'}
        code, out, err = RUNNER(control.status_command(lambda _: path), STATUS_TIMEOUT, environ)
        if control.kind == 'docker':
            if code != 0:
                return {'state': 'unknown', 'detail': 'docker inspect failed: ' + (redact(err, 160) or 'no details')}
            try:
                state = json.loads(out)
            except ValueError:
                return {'state': 'unknown', 'detail': 'docker inspect returned an unexpected answer.'}
            status = str(state.get('Status') or '')
            mapped = {'running': 'running', 'restarting': 'starting', 'exited': 'stopped', 'created': 'stopped',
                      'paused': 'stopped', 'dead': 'failed', 'removing': 'stopping'}.get(status, 'unknown')
            started = _parse_updated(str(state.get('StartedAt') or '')[:19] + '+00:00')
            pid = state.get('Pid') if isinstance(state.get('Pid'), int) and state.get('Pid') > 0 else None
            return {'state': mapped, 'service_pid': pid, 'detail': 'Container status: ' + (redact(status, 20) or '?'),
                    'uptime_seconds': round(_now() - started) if mapped == 'running' and started else None}
        if code != 0 and not out:
            return {'state': 'unknown', 'detail': 'systemctl failed: ' + (redact(err, 160) or 'no details')}
        props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
        if props.get('LoadState') == 'not-found':
            return {'state': 'unknown', 'detail': 'systemd does not know this unit.'}
        active = props.get('ActiveState', '')
        mapped = {'active': 'running', 'reloading': 'running', 'activating': 'starting', 'deactivating': 'stopping',
                  'inactive': 'stopped', 'failed': 'failed'}.get(active, 'unknown')
        pid = int(props['MainPID']) if props.get('MainPID', '').isdigit() and int(props['MainPID']) > 0 else None
        uptime, entered = _host_uptime(), props.get('ActiveEnterTimestampMonotonic', '')
        seconds = round(uptime - int(entered) / 1e6) if mapped == 'running' and uptime and entered.isdigit() \
            and int(entered) > 0 else None
        return {'state': mapped, 'service_pid': pid, 'uptime_seconds': seconds,
                'detail': f"systemd: {redact(active, 20) or '?'} ({redact(props.get('SubState'), 20) or '?'})"}
    return _cached(('service', control.kind, control.target), STATUS_CACHE_SECONDS, compute)


def gateway_status(home, environ, control):
    status, runtime = _pid_file_status(home)
    recorded_running = status['state'] == 'running'
    updated = _parse_updated(runtime.get('updated_at'))
    fresh = updated is not None and _now() - updated <= STATE_STALE_SECONDS
    if control is not None:
        service = _service_status(control, environ)
        if service and service.get('state') != 'unknown':
            detail = service.get('detail')
            if service['state'] == 'running' and not recorded_running and control.kind != 'docker':
                detail = (detail or '') + ('. No live gateway is recorded in this Hermes home: check that the unit '
                                           'runs with HERMES_HOME=' + str(home) + '.')
                status['mismatch'] = True
            status = dict(status, state=service['state'], detected_by=control.kind.split('-')[0], detail=detail)
            if service.get('uptime_seconds') is not None:
                status['uptime_seconds'] = service['uptime_seconds']
            if status['pid'] is None and service.get('service_pid') and control.kind != 'docker':
                status['pid'] = service['service_pid']
        elif service:
            status = dict(status, detail=' '.join(filter(None, (status.get('detail'), service.get('detail')))))
    platforms = []
    if status['state'] == 'running' and fresh and isinstance(runtime.get('platforms'), dict):
        for name, info in list(runtime['platforms'].items())[:20]:
            info = info if isinstance(info, dict) else {}
            platforms.append({'name': redact(name, 40), 'state': redact(info.get('state'), 30) or 'unknown',
                              'needs_attention': info.get('needs_attention') is True,
                              'error': redact(info.get('error_code'), 60)})
    status.update(platforms=platforms, inside_console=status.get('pid') == os.getpid(),
                  active_agents=runtime.get('active_agents') if isinstance(runtime.get('active_agents'), int) else None,
                  version=redact(runtime.get('code_version'), 30),
                  running_commit=runtime.get('code_sha') if isinstance(runtime.get('code_sha'), str)
                  and re.fullmatch(r'[0-9a-f]{7,64}', runtime['code_sha']) else None,
                  status_updated_at=redact(runtime.get('updated_at'), 40))
    return status


def scheduler_status(home):
    path = Path(home) / 'cron'
    if path.is_symlink() or not path.is_dir():
        return {'status': 'missing', 'age_seconds': None}
    raw = _read_small(path / 'ticker_heartbeat', 200)
    try:
        age = round(_now() - float(raw.decode('utf-8-sig').split()[0]))
    except (AttributeError, ValueError, IndexError, UnicodeDecodeError):
        return {'status': 'missing', 'age_seconds': None}
    return {'status': 'fresh' if 0 <= age <= HEARTBEAT_FRESH_SECONDS else 'stale', 'age_seconds': age}


def _loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def api_health(environ):
    raw = (environ.get('HERMES_CONSOLE_CHAT_API_URL') or '').strip()
    try:
        if raw:
            parsed = urlsplit(raw)
            scheme, host, port = parsed.scheme, parsed.hostname, parsed.port
            port = port or (443 if scheme == 'https' else 80)
        elif str(environ.get('API_SERVER_ENABLED') or '').strip().lower() in ('1', 'true', 'yes'):
            scheme, host = 'http', (environ.get('API_SERVER_HOST') or '127.0.0.1').strip()
            port = int(environ.get('API_SERVER_PORT') or 8642)
        else:
            return {'status': 'not_configured', 'detail': 'No Hermes API server is configured for the console.'}
        if scheme not in ('http', 'https') or not host or not 0 < port < 65536:
            raise ValueError()
    except ValueError:
        return {'status': 'invalid', 'detail': 'The configured API address is invalid.'}
    if not _loopback(host):
        return {'status': 'not_probed', 'detail': 'Only an API on this server (loopback) is probed.'}
    netloc = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
    url = f'{scheme}://{netloc}/health'

    def compute():
        code, error = HTTP_GET(url, HEALTH_TIMEOUT)
        if code == 200:
            return {'status': 'ok', 'detail': 'Responding on port ' + str(port), 'http_status': 200}
        if code is not None:
            return {'status': 'error', 'detail': f'Health check answered HTTP {code}.', 'http_status': code}
        return {'status': 'unreachable', 'detail': 'Nothing answered on port ' + str(port) + '.'}
    return _cached(('health', url), HEALTH_CACHE_SECONDS, compute)


def _hermes_source(environ):
    explicit = (environ.get('HERMES_SRC') or '').strip()
    candidates = [Path(explicit)] if explicit and Path(explicit).is_absolute() else []
    gateway = sys.modules.get('gateway')
    if getattr(gateway, '__file__', None):
        candidates.append(Path(gateway.__file__).resolve().parents[1])
    candidates += [REPO_ROOT / '.runtime' / 'hermes-agent', Path('/opt/hermes/hermes-agent'),
                   Path.home() / '.hermes' / 'hermes-agent']
    return next((path for path in candidates if path.is_dir()), None)


def _is_checkout(path):
    return path is not None and (path / '.git').exists() and not (path / '.git').is_symlink()


# ---- health checks ----------------------------------------------------------

def _check(id_, label, status, detail, hint=None):
    return {'id': id_, 'label': label, 'status': status, 'detail': detail, 'hint': hint}


def health_checks(home, environ, assistant_dir, *, deep=False, gateway=None, scheduler=None, backups=None,
                  resources=None):
    home = Path(home)
    checks = []
    config = _config(home)
    if config is None:
        checks.append(_check('config', 'Hermes configuration', 'fail', 'config.yaml was not found in the Hermes home.',
                             'Run the setup (scripts/run.sh Setup) or `hermes setup` for this home.'))
    elif config is False:
        checks.append(_check('config', 'Hermes configuration', 'fail', 'config.yaml could not be parsed.',
                             'Fix the YAML syntax; the gateway refuses to start with a broken config.'))
    else:
        checks.append(_check('config', 'Hermes configuration', 'pass', 'config.yaml parses.'))
    config = config or {}
    found = False
    for label, path in _env_files(home):
        try:
            info = os.lstat(path)
        except OSError:
            continue
        found = True
        if stat.S_ISLNK(info.st_mode):
            checks.append(_check('env-' + label.split()[0].lower(), f'.env ({label})', 'warn',
                                 'The secrets file is a symbolic link.', 'Replace it with a regular file.'))
        elif stat.S_IMODE(info.st_mode) & 0o077:
            checks.append(_check('env-' + label.split()[0].lower(), f'.env ({label})', 'warn',
                                 f'Readable by other users (mode {oct(stat.S_IMODE(info.st_mode))[2:]}).',
                                 f'chmod 600 {path}'))
        else:
            checks.append(_check('env-' + label.split()[0].lower(), f'.env ({label})', 'pass',
                                 'Only the owner can read it (0600).'))
    if not found:
        checks.append(_check('env', 'Secrets file (.env)', 'skip',
                             'No .env file found; variables may come from the service environment.'))
    if not home.is_dir():
        checks.append(_check('home', 'Hermes home writable', 'fail', 'The Hermes home folder does not exist.'))
    elif deep:
        try:
            fd, probe = None, home / ('.console-write-test-' + secrets.token_hex(4))
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.write(fd, b'ok')
            os.close(fd)
            os.unlink(probe)
            checks.append(_check('home', 'Hermes home writable', 'pass', 'A test file was written and removed.'))
        except OSError:
            checks.append(_check('home', 'Hermes home writable', 'fail', 'The console could not write a test file.',
                                 'Check the owner and permissions of the Hermes home folder.'))
    else:
        writable = os.access(home, os.W_OK)
        checks.append(_check('home', 'Hermes home writable', 'pass' if writable else 'fail',
                             'Writable by this account.' if writable else 'Not writable by this account.',
                             None if writable else 'Check the owner and permissions of the Hermes home folder.'))
    resources = resources or read_resources(home)
    free, used = resources.get('disk_free_bytes'), resources.get('disk_percent')
    if free is None:
        checks.append(_check('disk', 'Disk space', 'skip', 'Disk usage is unavailable here.'))
    elif free < 256 * 1024 ** 2:
        checks.append(_check('disk', 'Disk space', 'fail', 'Less than 256 MB free.',
                             'Delete old backups or logs; Hermes cannot save sessions on a full disk.'))
    elif free < 1024 ** 3 or (used or 0) > 90:
        checks.append(_check('disk', 'Disk space', 'warn', f'{used}% used; {round(free / 1024 ** 3, 1)} GB free.',
                             'Free some space soon.'))
    else:
        checks.append(_check('disk', 'Disk space', 'pass', f'{round(free / 1024 ** 3, 1)} GB free.'))
    plugin = home / 'plugins' / 'assistant-core'
    enabled = (config.get('plugins') or {}).get('enabled') if isinstance(config.get('plugins'), dict) else None
    enabled = enabled if isinstance(enabled, list) else []
    if not plugin.is_dir() or plugin.is_symlink():
        checks.append(_check('plugin', 'Assistant core plugin', 'warn', 'Not installed in this Hermes home.',
                             'Run scripts/run.sh Setup to install quotas, tool safety and telemetry.'))
    elif 'assistant-core' not in enabled:
        checks.append(_check('plugin', 'Assistant core plugin', 'warn', 'Installed but not enabled.',
                             'Add assistant-core to plugins.enabled in config.yaml and restart the gateway.'))
    else:
        checks.append(_check('plugin', 'Assistant core plugin', 'pass', 'Installed and enabled.'))
    telemetry = Path(assistant_dir) / 'telemetry.sqlite3'
    checks.append(_check('telemetry', 'Usage telemetry', 'pass' if telemetry.is_file() else 'warn',
                         'Telemetry database present.' if telemetry.is_file()
                         else 'No telemetry database yet (created after the first model call).'))
    try:
        from assistant_core.hermes_plugin import unsafe_toolsets
        unsafe = unsafe_toolsets(config)
        checks.append(_check('toolsets', 'Chat toolsets', 'fail' if unsafe else 'pass',
                             ('Host-level tools are enabled for: ' + ', '.join(redact(n, 30) or '?' for n in unsafe))
                             if unsafe else 'No host-level toolsets are exposed to chats.',
                             'Remove computer_use, desktop_ui, connections, setup and project from platform_toolsets.'
                             if unsafe else None))
    except Exception:
        checks.append(_check('toolsets', 'Chat toolsets', 'skip', 'The assistant core package is not available here.'))
    terminal = config.get('terminal') if isinstance(config.get('terminal'), dict) else {}
    backend = terminal.get('backend') if isinstance(terminal.get('backend'), str) else None
    if backend in (None, '', 'local'):
        checks.append(_check('sandbox', 'Command sandbox', 'warn',
                             'Commands run directly on this server (backend: local).',
                             'Set terminal.backend to docker (or another sandbox) in config.yaml.'))
    else:
        checks.append(_check('sandbox', 'Command sandbox', 'pass', 'Backend: ' + (redact(backend, 30) or '?')))
    platforms = config.get('platforms') if isinstance(config.get('platforms'), dict) else {}
    wanted = {name for name, spec in platforms.items() if isinstance(spec, dict) and spec.get('enabled') is True}
    if gateway:
        wanted |= {p['name'] for p in gateway.get('platforms') or [] if p.get('name') in CHANNEL_KEYS}
    present = {name for name, key in CHANNEL_KEYS.items() if _configured(key, home, environ)}
    missing = sorted(name for name in wanted if name in CHANNEL_KEYS and name not in present)
    if missing:
        checks.append(_check('channels', 'Channel tokens', 'fail', 'Missing token for: ' + ', '.join(missing),
                             'Add ' + ', '.join(CHANNEL_KEYS[m] for m in missing) + ' to .env and restart.'))
    elif present:
        checks.append(_check('channels', 'Channel tokens', 'pass', 'Token configured for: ' + ', '.join(sorted(present))))
    else:
        checks.append(_check('channels', 'Channel tokens', 'warn', 'No messaging channel token is configured.',
                             'Add TELEGRAM_BOT_TOKEN or ZALO_BOT_TOKEN to .env.'))
    host = str(environ.get('ZALO_ADMIN_HOST') or '127.0.0.1').strip()
    public = str(environ.get('ZALO_ADMIN_PUBLIC_URL') or '').strip()
    if host in ('127.0.0.1', 'localhost', '::1'):
        checks.append(_check('console', 'Console exposure', 'pass', 'Listening on this server only (loopback).'))
    elif public.startswith('https://'):
        checks.append(_check('console', 'Console exposure', 'pass', 'Published through an HTTPS origin.'))
    else:
        checks.append(_check('console', 'Console exposure', 'fail', 'Listening on all interfaces without HTTPS.',
                             'Set ZALO_ADMIN_HOST=127.0.0.1 or put the console behind an HTTPS proxy.'))
    if gateway is not None:
        state = gateway.get('state')
        checks.append(_check('gateway', 'Gateway running', 'pass' if state == 'running' else
                             'fail' if state == 'failed' else 'warn', gateway.get('detail') or 'State: ' + str(state)))
    if scheduler is not None and scheduler['status'] != 'missing':
        checks.append(_check('scheduler', 'Scheduler heartbeat', 'pass' if scheduler['status'] == 'fresh' else 'warn',
                             f"Last tick {scheduler['age_seconds']} s ago.",
                             None if scheduler['status'] == 'fresh' else 'Scheduled jobs run only while the gateway runs.'))
    if backups is not None:
        newest = backups[0]['created_at'] if backups else None
        stamp = _parse_updated(newest)
        if stamp is None:
            checks.append(_check('backup', 'Recent backup', 'warn', 'No console backup yet.', 'Create one below.'))
        else:
            age = (_now() - stamp) / 86400
            checks.append(_check('backup', 'Recent backup', 'pass' if age <= 7 else 'warn',
                                 f'Newest backup is {max(0, round(age))} day(s) old.',
                                 None if age <= 7 else 'Create a fresh backup.'))
    return checks


# ---- backups ----------------------------------------------------------------

def _plain_dir(path, create):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not create:
            return False
        os.mkdir(path, 0o700)
        info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AdminError('The backup folder is not a plain directory. Fix it on the server first.', 409)
    return True


def backup_dir(home, create=False):
    root = Path(home).resolve() / 'backups'
    target = root / 'console'
    if not _plain_dir(root, create) or not _plain_dir(target, create):
        return None
    if create and stat.S_IMODE(os.lstat(target).st_mode) & 0o077:
        os.chmod(target, 0o700)
    return target


def list_backups(home):
    try:
        folder = backup_dir(home)
    except AdminError:
        return []
    if folder is None:
        return []
    items = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for entry in entries:
        match = BACKUP_PATTERN.fullmatch(entry.name)
        if not match or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            continue
        try:
            created = datetime.strptime(match.group(1), '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)
            size = entry.stat(follow_symlinks=False).st_size
        except (ValueError, OSError):
            continue
        items.append({'id': entry.name, 'name': entry.name, 'size_bytes': size,
                      'created_at': created.isoformat(timespec='seconds'), 'includes_secrets': bool(match.group(2))})
    return sorted(items, key=lambda item: item['name'], reverse=True)


def restore_command(home, name):
    archive = Path(home).resolve() / 'backups' / 'console' / name
    return f'tar -xzpf {archive} -C {Path(home).resolve()} --strip-components=1 {ARCHIVE_ROOT}'


class _Abort(Exception):
    pass


def _is_secret(rel_parts, name):
    return name == '.env' or name.startswith('.env.') or (not rel_parts and name in SECRET_NAMES)


def _skip(rel_parts, name, is_dir, include_secrets, size):
    """Reason string to skip, or None. rel_parts are the parent directories."""
    if is_dir:
        if name in EXCLUDED_DIRS or (not rel_parts and name in ROOT_EXCLUDED_DIRS):
            return 'excluded'
        return None
    if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES) or name.startswith('.console-write-test-'):
        return 'excluded'
    if _is_secret(rel_parts, name):
        return None if include_secrets else 'secret'
    if rel_parts[:1] == ('logs',) and size > BACKUP_LOG_CAP:
        return 'large_log'
    return None


def create_backup(home, *, include_secrets=False, keep=BACKUP_KEEP_DEFAULT, progress=None, clock=_now):
    """Write a private tar.gz of the Hermes home; returns the manifest. Raises AdminError."""
    home = Path(home).resolve()
    if not home.is_dir():
        raise AdminError('The Hermes home folder does not exist.', 409)
    folder = backup_dir(home, create=True)
    stamp = datetime.fromtimestamp(clock(), timezone.utc)
    name = f"hermes-home-{stamp.strftime('%Y%m%d-%H%M%S')}{'-secrets' if include_secrets else ''}.tar.gz"
    final = folder / name
    if final.exists():
        raise AdminError('A backup was created this second. Wait a moment and try again.', 409)
    partial = folder / ('.' + name + '.' + secrets.token_hex(4) + '.partial')
    started = time.monotonic()
    stats = {'files': 0, 'bytes': 0, 'sqlite_snapshots': 0, 'sqlite_raw': 0}
    skipped = {'symlinks': 0, 'special': 0, 'excluded': 0, 'secret': 0, 'large_log': 0, 'unreadable': 0}
    secret_files = []
    try:
        if shutil.disk_usage(folder).free < BACKUP_MIN_FREE_BYTES:
            raise _Abort('Less than 512 MB of disk space is free, so no backup was started.')
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as raw, tarfile.open(fileobj=raw, mode='w:gz', format=tarfile.PAX_FORMAT) as tar:
            def guard():
                if time.monotonic() - started > BACKUP_MAX_SECONDS:
                    raise _Abort('The backup took longer than 30 minutes and was cancelled.')
                if stats['files'] > BACKUP_MAX_FILES:
                    raise _Abort('The Hermes home has too many files to back up from the console.')
                if stats['bytes'] > BACKUP_MAX_INPUT_BYTES:
                    raise _Abort('The Hermes home is larger than 4 GB; back it up from the server instead.')
                if stats['files'] % 200 == 0 and shutil.disk_usage(folder).free < BACKUP_MIN_FREE_BYTES:
                    raise _Abort('Less than 512 MB of disk space is left, so the backup was stopped.')

            def add_bytes(arcname, data, info):
                member = tarfile.TarInfo(arcname)
                member.size, member.mtime, member.mode = len(data), int(info.st_mtime), stat.S_IMODE(info.st_mode)
                tar.addfile(member, io.BytesIO(data))

            def add_dir(arcname, info):
                member = tarfile.TarInfo(arcname)
                member.type, member.mtime, member.mode = tarfile.DIRTYPE, int(info.st_mtime), stat.S_IMODE(info.st_mode)
                tar.addfile(member)

            snapshotted = set()
            stack = [(home, ())]
            add_dir(ARCHIVE_ROOT, os.lstat(home))
            while stack:
                directory, rel = stack.pop()
                try:
                    entries = sorted(os.scandir(directory), key=lambda e: e.name)
                except OSError:
                    skipped['unreadable'] += 1
                    continue
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        skipped['unreadable'] += 1
                        continue
                    arcname = '/'.join((ARCHIVE_ROOT, *rel, entry.name))
                    if stat.S_ISLNK(info.st_mode):
                        skipped['symlinks'] += 1
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if Path(entry.path) == folder or _skip(rel, entry.name, True, include_secrets, 0):
                            skipped['excluded'] += 1
                            continue
                        add_dir(arcname, info)
                        stack.append((Path(entry.path), rel + (entry.name,)))
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        skipped['special'] += 1
                        continue
                    reason = _skip(rel, entry.name, False, include_secrets, info.st_size)
                    base = next((entry.name[:-len(s)] for s in SQLITE_SIDECARS if entry.name.endswith(s)), None)
                    if reason is None and base and Path(directory, base) in snapshotted:
                        reason = 'excluded'
                    if reason:
                        skipped[reason] += 1
                        continue
                    if _is_secret(rel, entry.name):
                        secret_files.append('/'.join((*rel, entry.name)))
                    stats['files'] += 1
                    stats['bytes'] += info.st_size
                    guard()
                    if _add_file(tar, folder, Path(entry.path), arcname, info, snapshotted, stats, add_bytes) is False:
                        skipped['unreadable'] += 1
                    if progress and stats['files'] % 50 == 0:
                        progress(dict(stats))
            manifest = {'format': 1, 'created_at': stamp.isoformat(timespec='seconds'), 'hermes_home': str(home),
                        'archive_root': ARCHIVE_ROOT, 'includes_secrets': bool(include_secrets),
                        'secret_files': secret_files if include_secrets else [], 'files': stats['files'],
                        'input_bytes': stats['bytes'], 'sqlite_snapshots': stats['sqlite_snapshots'],
                        'sqlite_raw_copies': stats['sqlite_raw'], 'skipped': skipped,
                        'excluded_root_dirs': sorted(ROOT_EXCLUDED_DIRS), 'excluded_dirs': sorted(EXCLUDED_DIRS),
                        'log_cap_bytes': BACKUP_LOG_CAP, 'console_commit': _git_head(REPO_ROOT),
                        'restore': restore_command(home, name)}
            data = json.dumps(manifest, indent=2).encode()
            member = tarfile.TarInfo(MANIFEST)
            member.size, member.mtime, member.mode = len(data), int(stamp.timestamp()), 0o600
            tar.addfile(member, io.BytesIO(data))
        with open(partial, 'rb') as handle:
            os.fsync(handle.fileno())
        os.replace(partial, final)
    except _Abort as error:
        raise AdminError(str(error), 507 if 'disk' in str(error) else 413) from None
    except AdminError:
        raise
    except (OSError, tarfile.TarError, sqlite3.Error):
        raise AdminError('The backup could not be written. Check disk space and folder permissions.', 500) from None
    finally:
        try:
            os.unlink(partial)
        except OSError:
            pass
    removed = prune_backups(home, keep)
    return dict(manifest, name=name, size_bytes=final.stat().st_size, removed=removed)


def _add_file(tar, folder, path, arcname, info, snapshotted, stats, add_bytes):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
    except OSError:
        return False
    with os.fdopen(fd, 'rb') as handle:
        current = os.fstat(handle.fileno())
        if not stat.S_ISREG(current.st_mode):
            return False
        head = handle.read(16)
        if head == SQLITE_MAGIC:
            snapshot = _sqlite_snapshot(folder, path)
            if snapshot is not None:
                snapshotted.add(path)
                stats['sqlite_snapshots'] += 1
                try:
                    member = tarfile.TarInfo(arcname)
                    member.size, member.mtime = os.path.getsize(snapshot), int(current.st_mtime)
                    member.mode = stat.S_IMODE(current.st_mode)
                    with open(snapshot, 'rb') as copy:
                        tar.addfile(member, copy)
                finally:
                    os.unlink(snapshot)
                return True
            stats['sqlite_raw'] += 1
        handle.seek(0)
        if current.st_size <= MEMORY_READ_LIMIT:
            add_bytes(arcname, handle.read(MEMORY_READ_LIMIT + 1)[:MEMORY_READ_LIMIT], current)
            return True
        # Large, presumably stable file: stream exactly the size seen now.
        member = tarfile.TarInfo(arcname)
        member.size, member.mtime, member.mode = current.st_size, int(current.st_mtime), stat.S_IMODE(current.st_mode)
        tar.addfile(member, handle)
        return True


def _sqlite_snapshot(folder, path):
    """Consistent copy through the SQLite backup API into a private temp file, or None."""
    target = folder / ('.snapshot-' + secrets.token_hex(6) + '.partial')
    try:
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600))
        source = sqlite3.connect(f'file:{quote(str(path))}?mode=ro', uri=True, timeout=5)
        try:
            copy = sqlite3.connect(str(target))
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        return target
    except (sqlite3.Error, OSError):
        try:
            os.unlink(target)
        except OSError:
            pass
        return None


def prune_backups(home, keep):
    removed = []
    folder = backup_dir(home)
    if folder is None:
        return removed
    for item in list_backups(home)[keep:]:
        try:
            os.unlink(folder / item['name'])
            removed.append(item['name'])
        except OSError:
            pass
    cutoff = _now() - 6 * 3600
    for entry in os.scandir(folder):  # leftovers of interrupted runs
        try:
            if (entry.name.endswith('.partial') and entry.is_file(follow_symlinks=False)
                    and entry.stat(follow_symlinks=False).st_mtime < cutoff):
                os.unlink(entry.path)
        except OSError:
            pass
    return removed


def delete_backup(home, name):
    if not isinstance(name, str) or not BACKUP_PATTERN.fullmatch(name):
        raise AdminError('Choose a backup from the list.')
    folder = backup_dir(home)
    path = folder / name if folder else None
    try:
        info = os.lstat(path) if path else None
    except OSError:
        info = None
    if info is None or not stat.S_ISREG(info.st_mode):
        raise AdminError('That backup no longer exists. Refresh the page.', 404)
    os.unlink(path)


# ---- background jobs (one at a time per home) ------------------------------

_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _job(home):
    with _JOBS_LOCK:
        job = _JOBS.get(str(home))
        return dict(job, progress=dict(job.get('progress') or {})) if job else None


def _start_job(home, kind, label, work, synchronous=False):
    key = str(home)
    with _JOBS_LOCK:
        current = _JOBS.get(key)
        if current and current['status'] == 'running':
            raise AdminError('Another operation is still running. Wait for it to finish.', 409)
        job = {'id': secrets.token_hex(6), 'kind': kind, 'label': label, 'status': 'running', 'message': None,
               'started_at': _iso(_now()), 'finished_at': None, 'progress': {}}
        _JOBS[key] = job

    def progress(values):
        with _JOBS_LOCK:
            job['progress'] = values

    def run():
        try:
            message = work(progress)
            status = 'done'
        except AdminError as error:
            status, message = 'failed', str(error)
        except Exception:
            status, message = 'failed', 'The operation failed unexpectedly. Check the console log on the server.'
        with _JOBS_LOCK:
            job.update(status=status, message=message, finished_at=_iso(_now()))
    if synchronous:
        run()
    else:
        threading.Thread(target=run, name='console-ops-' + kind, daemon=True).start()
    return job


# ---- updates ----------------------------------------------------------------

def _git(path, args, environ):
    git = _which('git', environ)
    if not git:
        return 127, '', 'git is not installed.'
    return RUNNER([git, '-C', str(path), *args], GIT_TIMEOUT, environ)


def check_updates(path, environ):
    local = _git_head(path)
    code, out, _ = _git(path, ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}'], environ)
    remote, ref = 'origin', 'HEAD'
    upstream = out.strip()
    if code == 0 and '/' in upstream:
        remote, branch = upstream.split('/', 1)
        ref = 'refs/heads/' + branch
    if not _GIT_REF.fullmatch(remote) or not _GIT_REF.fullmatch(ref) or '..' in ref:
        return {'status': 'unknown', 'local': local, 'remote': None, 'message': 'The upstream branch name is unusual.'}
    code, out, err = _git(path, ['ls-remote', '--quiet', remote, ref], environ)
    if code != 0:
        return {'status': 'unknown', 'local': local, 'remote': None,
                'message': 'Could not reach the upstream repository: ' + (redact(err, 120) or 'no details')}
    remote_sha = next((line.split()[0] for line in out.splitlines() if line.split() and _SHA.fullmatch(line.split()[0])),
                      None)
    if remote_sha is None:
        return {'status': 'unknown', 'local': local, 'remote': None, 'message': 'The upstream branch was not found.'}
    if remote_sha == local:
        return {'status': 'current', 'local': local, 'remote': remote_sha, 'message': 'Up to date.'}
    known = _git(path, ['merge-base', '--is-ancestor', remote_sha, 'HEAD'], environ)[0] == 0
    if known:
        return {'status': 'current', 'local': local, 'remote': remote_sha,
                'message': 'Up to date (this checkout is ahead of upstream).'}
    return {'status': 'available', 'local': local, 'remote': remote_sha,
            'message': f"A newer commit is published upstream ({remote_sha[:10]})."}


def update_commands(src, home, control):
    commands = []
    if src is not None and (src / '.hermes' / 'bin' / 'hermes').is_file():
        commands.append({'label': 'Update Hermes', 'command': f'HERMES_HOME={home} {src}/.hermes/bin/hermes update'})
    elif _is_checkout(src):
        commands.append({'label': 'Update Hermes', 'command': f'git -C {src} pull --ff-only'})
    elif control is not None and control.kind == 'docker':
        commands.append({'label': 'Update Hermes (Docker)',
                         'command': 'docker compose build --pull && docker compose up -d'})
    if _is_checkout(REPO_ROOT):
        commands.append({'label': 'Update this console and plugins',
                         'command': f'git -C {REPO_ROOT} pull --ff-only && {REPO_ROOT}/scripts/run.sh Update'})
    commands.append({'label': 'Then restart the gateway',
                     'command': control.display('restart') if control else 'systemctl --user restart hermes-gateway.service'})
    return commands


# ---- the section ------------------------------------------------------------

_LAST_CHECKS = {}
_LAST_UPDATES = {}


def _control_info(control, error, home):
    examples = [
        {'label': 'systemd user service (scripts/run.sh or hermes gateway install)',
         'value': f'{CONTROL_ENV}=systemd-user:hermes-gateway.service'},
        {'label': 'Docker Compose container', 'value': f'{CONTROL_ENV}=docker:hermes-zalo-bot'},
        {'label': 'System service with a sudo rule', 'value': f'{CONTROL_ENV}=systemd-sudo:hermes-gateway.service'}]
    if control is None:
        return {'enabled': False, 'error': error, 'examples': examples,
                'commands': [{'label': 'Restart a systemd user service', 'command': 'systemctl --user restart hermes-gateway.service'},
                             {'label': 'Restart the Docker container', 'command': 'docker restart hermes-zalo-bot'},
                             {'label': 'Foreground run (this repo)', 'command': 'scripts/run.sh Start  (stop with Ctrl+C)'}]}
    return {'enabled': True, 'kind': control.kind, 'target': control.target, 'error': None,
            'commands': [{'label': verb.title(), 'command': control.display(verb)} for verb in VERBS]}


def _revision(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _actions(revision, control, gateway, backups, can_check_updates, job_running):
    actions = [{'id': 'run_checks', 'label': 'Run checks', 'revision': revision,
                'description': 'Re-run every health check now, including a write test in the Hermes home.'}]
    if not job_running:
        actions.append({'id': 'create_backup', 'label': 'Create backup', 'revision': revision,
                        'description': 'Archive the Hermes home into backups/console on this server. '
                                       'Caches, runtimes, large logs and symlinks are skipped.',
                        'fields': [
                            {'name': 'include_secrets', 'label': 'Include secrets (.env and auth.json)', 'type': 'checkbox',
                             'value': False, 'help': 'Anyone holding a backup with secrets can use your bot and API keys.'},
                            {'name': 'keep', 'label': 'Backups to keep', 'type': 'number', 'value': BACKUP_KEEP_DEFAULT,
                             'help': f'Older console backups are deleted after this one succeeds (1-{BACKUP_KEEP_MAX}).'}]})
    for item in backups:
        actions.append({'id': 'delete_backup', 'label': 'Delete', 'revision': revision, 'target': item['id'],
                        'description': 'Permanently delete ' + item['name'] + ' from the server.',
                        'fields': [{'name': 'backup', 'type': 'hidden', 'value': item['name']}]})
    if can_check_updates:
        actions.append({'id': 'check_updates', 'label': 'Check for updates', 'revision': revision,
                        'description': 'Ask the upstream git repositories for their latest commit. Nothing is installed.'})
    if control is not None and not job_running:
        state = gateway.get('state')
        warn = ' This console runs inside the gateway, so the page will disconnect briefly.' \
            if gateway.get('inside_console') else ''
        if state in ('stopped', 'failed', 'unknown'):
            actions.append({'id': 'gateway_start', 'label': 'Start gateway', 'revision': revision,
                            'description': 'Runs: ' + control.display('start')})
        if state in ('running', 'starting', 'failed', 'unknown'):
            actions.append({'id': 'gateway_restart', 'label': 'Restart gateway', 'revision': revision,
                            'description': 'Runs: ' + control.display('restart') + '. Conversations in progress may be cut off.' + warn})
        if state in ('running', 'starting', 'unknown'):
            stop_warn = (' This console runs inside the gateway: stopping closes this page and it must be started '
                         'from the server.') if gateway.get('inside_console') else ''
            actions.append({'id': 'gateway_stop', 'label': 'Stop gateway', 'revision': revision,
                            'description': 'Runs: ' + control.display('stop') + '. The assistant stops answering.' + stop_warn,
                            'fields': [{'name': 'confirm_name', 'label': 'Type ' + control.target + ' to confirm',
                                        'type': 'text', 'required': True, 'max_length': 140}]})
    return actions


def _live(ctx):
    home = Path(ctx.home)
    environ = ctx.environ
    control, control_error = parse_control(environ.get(CONTROL_ENV))
    resources = read_resources(home)
    HISTORY.ensure(home)
    if not HISTORY.points:
        HISTORY.record(_now(), resources)
    gateway = gateway_status(home, environ, control)
    scheduler = scheduler_status(home)
    backups = list_backups(home)
    job = _job(home)
    config = _config(home) or {}
    terminal = config.get('terminal') if isinstance(config.get('terminal'), dict) else {}
    src = _hermes_source(environ)
    deep = _LAST_CHECKS.get(str(home))
    if deep and _now() - deep['stamp'] > DEEP_CHECKS_SECONDS:
        deep = None
    checks = deep['items'] if deep else health_checks(home, environ, ctx.assistant_dir, gateway=gateway,
                                                      scheduler=scheduler, backups=backups, resources=resources)
    job_running = bool(job and job['status'] == 'running')
    revision = _revision(environ.get(CONTROL_ENV), gateway['state'], gateway['pid'],
                         [b['name'] for b in backups], job and (job['id'], job['status']))
    updates = _LAST_UPDATES.get(str(home))
    versions = {'hermes_version': gateway.get('version'), 'hermes_running_commit': gateway.get('running_commit'),
                'hermes_source_commit': _git_head(src) if src else None, 'hermes_source': str(src) if src else None,
                'hermes_source_is_git': _is_checkout(src), 'console_commit': _git_head(REPO_ROOT),
                'bot_mode': redact((environ.get('ZALO_BOT_MODE') or 'lab').strip().lower(), 20),
                'sandbox_backend': redact(terminal.get('backend'), 30) if isinstance(terminal.get('backend'), str) else None,
                'mode': ctx.mode}
    can_update = _is_checkout(src) or _is_checkout(REPO_ROOT)
    return {
        'section': 'operations', 'available': True, 'source': 'system', 'updated_at': _iso(_now()),
        'revision': revision, 'items': checks, 'home': str(home),
        'gateway': gateway, 'scheduler': scheduler, 'api_server': api_health(environ), 'versions': versions,
        'resources': resources, 'pressure': pressure(resources), 'history': HISTORY.series(),
        'control': _control_info(control, control_error, home),
        'checks': {'ran_at': deep['ran_at'] if deep else None, 'deep': bool(deep), 'items': checks,
                   'summary': {s: sum(c['status'] == s for c in checks) for s in ('pass', 'warn', 'fail', 'skip')}},
        'backups': {'folder': str(home.resolve() / 'backups' / 'console'), 'items': backups,
                    'total_bytes': sum(b['size_bytes'] for b in backups), 'keep_default': BACKUP_KEEP_DEFAULT,
                    'restore_command': restore_command(home, backups[0]['name'] if backups else '<backup-name>.tar.gz'),
                    'stop_first': control.display('stop') if control else 'systemctl --user stop hermes-gateway.service'},
        'job': job,
        'updates': {'available': can_update, 'checked_at': updates['checked_at'] if updates else None,
                    'results': updates['results'] if updates else [], 'commands': update_commands(src, home, control)},
        'actions': _actions(revision, control, gateway, backups, can_update, job_running),
    }


def snapshot(ctx, query):
    if ctx.demo:
        return _demo_snapshot()
    return _live(ctx)


def _keep(values):
    keep = values.get('keep', BACKUP_KEEP_DEFAULT)
    if keep is None:
        return BACKUP_KEEP_DEFAULT
    if isinstance(keep, bool) or not isinstance(keep, (int, float)) or keep != int(keep) \
            or not 1 <= int(keep) <= BACKUP_KEEP_MAX:
        raise AdminError(f'Backups to keep must be a whole number from 1 to {BACKUP_KEEP_MAX}.')
    return int(keep)


def _only(values, allowed):
    if set(values) - set(allowed):
        raise AdminError('Unexpected fields for this action.')


ACTION_IDS = frozenset({'run_checks', 'create_backup', 'delete_backup', 'check_updates',
                        'gateway_start', 'gateway_stop', 'gateway_restart'})


def _descriptor(current, action_id, revision, values):
    """The descriptor the owner confirmed, re-derived from fresh server state."""
    if action_id not in ACTION_IDS:
        raise AdminError('Unsupported action for this page.')
    if revision != current['revision']:
        raise AdminError('This page is out of date. Refresh and try again.', 409)
    for item in current['actions']:
        if item['id'] == action_id and (action_id != 'delete_backup' or item.get('target') == values.get('backup')):
            return item
    raise AdminError('That action is not available right now. Refresh the page.', 409)


def action(ctx, data):
    action_id, revision, values = validate_action(data)
    if ctx.demo:
        return _demo_action(action_id, revision, values)
    home = Path(ctx.home)
    environ = ctx.environ
    current = _live(ctx)
    _descriptor(current, action_id, revision, values)
    if action_id == 'run_checks':
        _only(values, ())
        items = health_checks(home, environ, ctx.assistant_dir, deep=True, gateway=current['gateway'],
                              scheduler=current['scheduler'], backups=current['backups']['items'],
                              resources=read_resources(home))
        _LAST_CHECKS[str(home)] = {'ran_at': _iso(_now()), 'stamp': _now(), 'items': items}
        failed = sum(c['status'] == 'fail' for c in items)
        warned = sum(c['status'] == 'warn' for c in items)
        return {'ok': True, 'message': f'Checks finished: {failed} failing, {warned} warning(s).'}
    if action_id == 'create_backup':
        _only(values, ('include_secrets', 'keep'))
        include = values.get('include_secrets', False)
        if not isinstance(include, bool):
            raise AdminError('Choose whether to include secrets.')
        keep = _keep(values)
        _start_job(home, 'backup', 'Creating backup', lambda progress: _backup_message(
            create_backup(home, include_secrets=include, keep=keep, progress=progress)))
        return {'ok': True, 'message': 'Backup started. This page updates while it runs.'}
    if action_id == 'delete_backup':
        _only(values, ('backup',))
        delete_backup(home, values['backup'])
        return {'ok': True, 'message': 'Deleted ' + values['backup'] + '.'}
    if action_id == 'check_updates':
        _only(values, ())
        results = []
        src = _hermes_source(environ)
        for key, label, path in (('hermes', 'Hermes Agent', src), ('console', 'This console and plugins', REPO_ROOT)):
            if _is_checkout(path):
                results.append(dict(check_updates(path, environ), id=key, label=label))
        _LAST_UPDATES[str(home)] = {'checked_at': _iso(_now()), 'results': results}
        pending = [r['label'] for r in results if r['status'] == 'available']
        return {'ok': True, 'message': ('Updates available for: ' + ', '.join(pending)) if pending
                else 'No updates found.' if results else 'No git checkout to check.'}
    verb = action_id.split('_', 1)[1]
    control, _ = parse_control(environ.get(CONTROL_ENV))
    if control is None:
        raise AdminError(f'Gateway control is off. Set {CONTROL_ENV} on the server to enable it.', 403)
    if verb == 'stop':
        _only(values, ('confirm_name',))
        if values.get('confirm_name') != control.target:
            raise AdminError('Type the exact service name to stop the gateway.')
    else:
        _only(values, ())
    return _run_control(home, environ, control, verb)


def _backup_message(result):
    removed = len(result.get('removed') or [])
    return f"Saved {result['name']} ({result['files']} files)." + (f' Removed {removed} older backup(s).' if removed else '')


def _run_control(home, environ, control, verb):
    tool = 'docker' if control.kind == 'docker' else 'systemctl'
    paths = {name: _which(name, environ) for name in (tool, 'sudo' if control.kind == 'systemd-sudo' else tool)}
    if not all(paths.values()):
        missing = next(name for name, path in paths.items() if not path)
        raise AdminError(f'{missing} was not found on the server PATH.', 503)
    argv = control.command(verb, lambda name: paths[name])

    def work(progress):
        code, _, err = RUNNER(argv, CONTROL_TIMEOUT, environ)
        _forget('service')
        if code != 0:
            hint = ' sudo needs a NOPASSWD rule for this exact command.' if control.kind == 'systemd-sudo' else ''
            raise AdminError(f'{verb.title()} failed (exit {code}): ' + (redact(err, 200) or 'no details') + '.' + hint)
        return {'start': 'Start requested.', 'stop': 'Stop requested.', 'restart': 'Restart requested.'}[verb] \
            + ' The status below updates within a few seconds.'
    # systemd queues the job (--no-block) and returns at once; docker waits for the container.
    job = _start_job(home, 'gateway', verb.title() + ' gateway', work, synchronous=control.kind != 'docker')
    if job['status'] == 'failed':
        raise AdminError(job['message'], 502)
    return {'ok': True, 'message': job['message'] or f'{verb.title()} requested. This page updates while it runs.'}


# ---- demo -------------------------------------------------------------------

_DEMO = {}
_DEMO_LOCK = threading.Lock()
DEMO_HOME = '/srv/hermes/.runtime/hermes-home'


def _demo_state():
    if not _DEMO:
        _DEMO.update(gateway='running', pid=41234, started=_now() - 3 * 86400 - 7200, checks_ran=None,
                     updates=None, job=None, backups=[
                         {'id': 'hermes-home-20261031-020000.tar.gz', 'name': 'hermes-home-20261031-020000.tar.gz',
                          'size_bytes': 48_300_000, 'created_at': '2026-10-31T02:00:00+00:00', 'includes_secrets': False},
                         {'id': 'hermes-home-20261024-020000-secrets.tar.gz',
                          'name': 'hermes-home-20261024-020000-secrets.tar.gz', 'size_bytes': 45_100_000,
                          'created_at': '2026-10-24T02:00:00+00:00', 'includes_secrets': True}])
    return _DEMO


def _demo_snapshot():
    with _DEMO_LOCK:
        state = _demo_state()
        control = Control('systemd-user', 'hermes-gateway.service')
        running = state['gateway'] == 'running'
        gateway = {'state': state['gateway'], 'pid': state['pid'] if running else None,
                   'uptime_seconds': round(_now() - state['started']) if running else None,
                   'detected_by': 'systemd', 'detail': 'systemd: active (running)' if running else 'systemd: inactive (dead)',
                   'platforms': [{'name': 'telegram', 'state': 'connected', 'needs_attention': False, 'error': None},
                                 {'name': 'zalo', 'state': 'connected', 'needs_attention': False, 'error': None}]
                   if running else [], 'inside_console': False, 'active_agents': 1 if running else None,
                   'version': '0.21.6', 'running_commit': 'dce1e9b37581dd62e480a9064dc04a709c2940d3',
                   'status_updated_at': _iso(_now() - 20)}
        resources = {'cpu_count': 2, 'load_1m': 0.64, 'load_5m': 0.52, 'load_15m': 0.47,
                     'memory_total_bytes': 4 * 1024 ** 3, 'memory_used_bytes': int(2.9 * 1024 ** 3),
                     'memory_available_bytes': int(1.1 * 1024 ** 3), 'swap_total_bytes': 2 * 1024 ** 3,
                     'swap_used_bytes': 300 * 1024 ** 2, 'disk_total_bytes': 80 * 1024 ** 3,
                     'disk_used_bytes': 31 * 1024 ** 3, 'disk_free_bytes': 49 * 1024 ** 3,
                     'host_uptime_seconds': 12 * 86400, 'memory_percent': 72.5, 'disk_percent': 38.8, 'swap_percent': 14.6}
        now = _now()
        points = [{'t': _iso(now - (47 - i) * 1800), 'samples': 30,
                   'load_1m': round(0.3 + 0.25 * ((i * 7) % 11) / 10, 2), 'load_peak': round(0.5 + (i % 5) / 10, 2),
                   'memory_percent': round(64 + (i * 5) % 13, 1), 'disk_percent': 38.5} for i in range(48)]
        checks = [_check('config', 'Hermes configuration', 'pass', 'config.yaml parses.'),
                  _check('env-hermes', '.env (Hermes home)', 'pass', 'Only the owner can read it (0600).'),
                  _check('home', 'Hermes home writable', 'pass', 'Writable by this account.'),
                  _check('disk', 'Disk space', 'pass', '49.0 GB free.'),
                  _check('plugin', 'Assistant core plugin', 'pass', 'Installed and enabled.'),
                  _check('telemetry', 'Usage telemetry', 'pass', 'Telemetry database present.'),
                  _check('toolsets', 'Chat toolsets', 'pass', 'No host-level toolsets are exposed to chats.'),
                  _check('sandbox', 'Command sandbox', 'pass', 'Backend: docker'),
                  _check('channels', 'Channel tokens', 'pass', 'Token configured for: telegram, zalo'),
                  _check('console', 'Console exposure', 'pass', 'Listening on this server only (loopback).'),
                  _check('gateway', 'Gateway running', 'pass' if running else 'warn', gateway['detail']),
                  _check('scheduler', 'Scheduler heartbeat', 'pass', 'Last tick 25 s ago.'),
                  _check('backup', 'Recent backup', 'pass' if state['backups'] else 'warn',
                         'Newest backup is 1 day(s) old.' if state['backups'] else 'No console backup yet.')]
        job = dict(state['job']) if state['job'] else None
        revision = _revision('demo', state['gateway'], [b['name'] for b in state['backups']], job and job['id'])
        updates = state['updates']
        return {
            'section': 'operations', 'available': True, 'source': 'demo', 'updated_at': _iso(now), 'revision': revision,
            'message': 'Synthetic demo data. Actions are simulated and nothing on a server changes.',
            'items': checks, 'home': DEMO_HOME, 'gateway': gateway,
            'scheduler': {'status': 'fresh' if running else 'stale', 'age_seconds': 25 if running else 900},
            'api_server': {'status': 'ok' if running else 'unreachable',
                           'detail': 'Responding on port 8642' if running else 'Nothing answered on port 8642.'},
            'versions': {'hermes_version': '0.21.6', 'hermes_running_commit': gateway['running_commit'],
                         'hermes_source_commit': gateway['running_commit'], 'hermes_source': '/srv/hermes/hermes-agent',
                         'hermes_source_is_git': True, 'console_commit': '01ef6a0c0ffee000000000000000000000000000',
                         'bot_mode': 'assistant', 'sandbox_backend': 'docker', 'mode': 'demo'},
            'resources': resources, 'pressure': [],
            'history': {'sample_seconds': SAMPLE_SECONDS, 'bucket_seconds': 1800, 'samples': 1440, 'points': points},
            'control': _control_info(control, None, DEMO_HOME),
            'checks': {'ran_at': state['checks_ran'], 'deep': bool(state['checks_ran']), 'items': checks,
                       'summary': {s: sum(c['status'] == s for c in checks) for s in ('pass', 'warn', 'fail', 'skip')}},
            'backups': {'folder': DEMO_HOME + '/backups/console', 'items': [dict(b) for b in state['backups']],
                        'total_bytes': sum(b['size_bytes'] for b in state['backups']), 'keep_default': BACKUP_KEEP_DEFAULT,
                        'restore_command': restore_command(DEMO_HOME, state['backups'][0]['name']
                                                           if state['backups'] else '<backup-name>.tar.gz'),
                        'stop_first': control.display('stop')},
            'job': job,
            'updates': {'available': True, 'checked_at': updates and updates['checked_at'],
                        'results': updates['results'] if updates else [],
                        'commands': [{'label': 'Update Hermes', 'command': 'HERMES_HOME=' + DEMO_HOME +
                                      ' /srv/hermes/hermes-agent/.hermes/bin/hermes update'},
                                     {'label': 'Then restart the gateway', 'command': control.display('restart')}]},
            'actions': _actions(revision, control, gateway, state['backups'], True, False),
        }


def _demo_action(action_id, revision, values):
    """Simulated in memory: no subprocess, disk or network."""
    _descriptor(_demo_snapshot(), action_id, revision, values)
    with _DEMO_LOCK:
        state = _demo_state()
        if action_id == 'run_checks':
            _only(values, ())
            state['checks_ran'] = _iso(_now())
            return {'ok': True, 'message': 'Demo: checks finished, 0 failing, 0 warning(s).'}
        if action_id == 'create_backup':
            _only(values, ('include_secrets', 'keep'))
            include = values.get('include_secrets', False) is True
            keep = _keep(values)
            stamp = datetime.fromtimestamp(_now(), timezone.utc)
            name = f"hermes-home-{stamp.strftime('%Y%m%d-%H%M%S')}{'-secrets' if include else ''}.tar.gz"
            if any(b['name'] == name for b in state['backups']):
                raise AdminError('A backup was created this second. Wait a moment and try again.', 409)
            state['backups'] = ([{'id': name, 'name': name, 'size_bytes': 48_900_000,
                                  'created_at': stamp.isoformat(timespec='seconds'), 'includes_secrets': include}]
                                + state['backups'])[:keep]
            state['job'] = {'id': secrets.token_hex(6), 'kind': 'backup', 'label': 'Creating backup', 'status': 'done',
                            'message': f'Demo: saved {name}.', 'started_at': _iso(_now()), 'finished_at': _iso(_now()),
                            'progress': {'files': 1834, 'bytes': 152_000_000}}
            return {'ok': True, 'message': 'Demo: backup simulated. Nothing was written.'}
        if action_id == 'delete_backup':
            _only(values, ('backup',))
            state['backups'] = [b for b in state['backups'] if b['name'] != values['backup']]
            return {'ok': True, 'message': 'Demo: deleted ' + values['backup'] + '. Nothing was removed on disk.'}
        if action_id == 'check_updates':
            _only(values, ())
            state['updates'] = {'checked_at': _iso(_now()), 'results': [
                {'id': 'hermes', 'label': 'Hermes Agent', 'status': 'available', 'local': 'dce1e9b37581',
                 'remote': 'f00dfeed1234', 'message': 'A newer commit is published upstream (f00dfeed12).'},
                {'id': 'console', 'label': 'This console and plugins', 'status': 'current', 'local': '01ef6a0c0ffe',
                 'remote': '01ef6a0c0ffe', 'message': 'Up to date.'}]}
            return {'ok': True, 'message': 'Demo: updates available for Hermes Agent.'}
        verb = action_id.split('_', 1)[1]
        if verb == 'stop':
            _only(values, ('confirm_name',))
            if values.get('confirm_name') != 'hermes-gateway.service':
                raise AdminError('Type the exact service name to stop the gateway.')
        else:
            _only(values, ())
        state['gateway'] = 'stopped' if verb == 'stop' else 'running'
        if verb != 'stop':
            state['started'], state['pid'] = _now(), state['pid'] + 7
        return {'ok': True, 'message': f'Demo: {verb} simulated. No process was touched.'}


register(Section('operations', snapshot, action))
