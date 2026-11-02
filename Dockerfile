# Use the Docker Desktop/Railway bundled BuildKit frontend (heredoc support).
# No separately downloaded Dockerfile frontend image is required.
# Hermes at this revision supports Python 3.14 at runtime. Its wider metadata
# range exists only so old installations can run their updater.
FROM python:3.14-slim-bookworm AS hermes-build

# Keep this pin in sync with scripts/install-hermes.ps1 after reviewing updates.
ARG HERMES_COMMIT=93c9360a8da592cee43e8895403e91c7a920b0ce
ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    HERMES_RUNTIME_DIR=/opt/hermes/tools \
    HERMES_HOME=/tmp/hermes-build-home \
    CC=gcc \
    CXX=g++

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update --error-on=any \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
      ca-certificates git build-essential libffi-dev libatomic1 pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/hermes/hermes-agent
RUN git init . \
    && git remote add origin https://github.com/NousResearch/hermes-agent.git \
    && git fetch --depth 1 origin "${HERMES_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${HERMES_COMMIT}" \
    && rm -rf .git

# Use Hermes' own pinned, hash-verified package manager. No pip installation
# of Hermes, moving latest tag, browser downloads or web/TUI build is needed
# for this headless Zalo worker. `all` includes the Google authentication extra.
RUN python -c 'from pm import ensure; ensure("uv", explicit=True); ensure("python", explicit=True)'
RUN python -c 'from pathlib import Path; from pm import installed_package, stage_manager_runtime; from scripts.bundles.payload import seal_pm_runtime; root = Path("/opt/hermes"); runtime_python = installed_package("python").binary; stage_manager_runtime(python=runtime_python, destination=root / "pm-runtime", project=root / "hermes-agent/pm"); seal_pm_runtime(root, runtime_python)'
RUN python -m pm.build_env --source /opt/hermes/hermes-agent \
    --out /opt/hermes/venv --no-install-project --sealed --extra all

# Upstream's offline assembler binds source, dependencies and generated
# launchers. A sealed runtime stays in the image when /data is replaced.
RUN HERMES_COMMIT="${HERMES_COMMIT}" python - <<'PY'
import json
import os
from pathlib import Path
from pm import installed_package
from pm.store import current_target
from scripts.build.agent import assemble
from scripts.build.inputs import AgentInputs, RESOURCE_ENV, dependency_site

root = Path("/opt/hermes")
source = root / "hermes-agent"
environment = root / "venv"
runtime_python = installed_package("python").binary
lock = json.loads((source / "pm/lock.json").read_text())
stamp = source / "install-stamp.json"
stamp.write_text(json.dumps({
    "schemaVersion": 2,
    "commit": os.environ["HERMES_COMMIT"],
    "distribution": "docker",
    "source": "hermes-zalo-bot Dockerfile",
    "updateMechanism": "external",
    "pmRuntime": str(root / "pm-runtime"),
}) + "\n")
assemble(AgentInputs(
    project=source / "pyproject.toml", code=source,
    repo="hermes-agent", placement="fixed", target=current_target(),
    python=runtime_python, environment=environment,
    site_packages=dependency_site(environment, lock["packages"]["python"]["version"], current_target()),
    pm_runtime=root / "pm-runtime", tools=root / "tools", stamp=stamp,
    resources={name: source / name for name in RESOURCE_ENV},
    frontends={}, ref=os.environ["HERMES_COMMIT"],
), root)
PY
RUN /opt/hermes/bin/hermes --version
RUN /opt/hermes/venv/bin/python -c 'import hermes_bootstrap; import google.auth, httpx; from ruamel.yaml import YAML; from gateway.platforms.base import BasePlatformAdapter; from gateway.config import PlatformConfig'

FROM python:3.14-slim-bookworm AS runtime
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update --error-on=any \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
      ca-certificates libatomic1 libstdc++6 libgomp1 tzdata tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=hermes-build /opt/hermes /opt/hermes
RUN ln -s /opt/hermes/bin/hermes /usr/local/bin/hermes

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir '.[sheets,backup]'
COPY plugin/ ./plugin/
COPY config/hermes.config.example.yaml ./config/hermes.config.example.yaml
# Bundled benchmark suites for the console and `python -m assistant_core.bench`.
COPY config/bench/ ./config/bench/
ENV HERMES_BENCH_BUNDLED_DIR=/app/config/bench
COPY scripts/nlu_check.py ./scripts/nlu_check.py

# PyYAML belongs to the system-Python bootstrap installed above; Hermes uses
# ruamel.yaml. Exercise the actual copied plugin with Hermes' sealed runtime,
# instead of requiring bootstrap-only packages inside the Hermes environment.
# All state and fake credentials below are confined to a disposable build dir.
RUN python - <<'PY'
import os
from pathlib import Path
import subprocess
import tempfile

from hermes_zalo.container_runtime import prepare

plugin_smoke = r'''
import asyncio
import importlib
import os
from pathlib import Path
import socket

def deny_network(*args, **kwargs):
    raise AssertionError("The image smoke test must not access the network")

socket.create_connection = deny_network
socket.socket.connect = deny_network
import hermes_bootstrap
import google.auth
import httpx
from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry
from hermes_cli.plugins import PluginManager
from hermes_state_registry import close_all_under

home = Path(os.environ["HERMES_HOME"])
manager = PluginManager(scope_key=str(home))
try:
    manager.discover_and_load()
    entry = platform_registry.get("zalo")
    assert entry is not None, "Hermes did not load the Zalo plugin"

    async def check_adapter():
        adapter = entry.adapter_factory(PlatformConfig(enabled=True))
        module = importlib.import_module(adapter.__class__.__module__)
        assert Path(module.__file__).resolve().is_relative_to(home / "plugins" / "zalo-platform")
        module.assert_chat_only()
        assert not adapter.__abstractmethods__
        assert not entry.trusted_inbound and not entry.allow_update_command
        await adapter.disconnect()

    asyncio.run(check_adapter())
finally:
    manager.unload()
    close_all_under(home)
print("Sealed Hermes loaded the copied Zalo plugin; tools and network are disabled.")
'''
with tempfile.TemporaryDirectory(prefix="zalo-image-smoke-") as directory:
    values = dict(os.environ)
    values.update(
        BOT_DATA_DIR=directory, HERMES_HOME=str(Path(directory) / "hermes-home"),
        HERMES_PROVIDER="openrouter", HERMES_MODEL="smoke/model",
        ZALO_BOT_TOKEN="image-smoke-fake-token", ZALO_ALLOWED_USERS="smoke-user",
        HERMES_RUNTIME_DIR="/opt/hermes/tools", HERMES_DISABLE_LAZY_INSTALLS="1",
    )
    prepare(values)
    subprocess.run(["/opt/hermes/venv/bin/python", "-c", plugin_smoke],
                   env=values, cwd="/app", check=True, timeout=120)
PY

# Zalo uses outbound polling; Admin is opt-in, behind the configured HTTPS
# origin/proxy and the same persistent volume. No public listener by default.
# Secrets are runtime
# variables or files on the volume, never build arguments or copied .env files.
ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Ho_Chi_Minh \
    BOT_DATA_DIR=/data \
    BOT_START_ENABLED=false \
    HERMES_HOME=/data/hermes-home \
    HERMES_EXECUTABLE=/usr/local/bin/hermes \
    HERMES_PYTHON=/opt/hermes/venv/bin/python \
    HERMES_RUNTIME_DIR=/opt/hermes/tools \
    HERMES_DISABLE_LAZY_INSTALLS=1
STOPSIGNAL SIGTERM
# Docker/Compose liveness probe (Railway ignores it). It passes in standby or
# while the console is off, otherwise GETs http://127.0.0.1:<port>/healthz,
# which returns only {"status": "ok"} and needs no login, Host or proxy key.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-m", "hermes_zalo.admin_cli", "healthcheck"]
# Reap orphaned children and forward shutdown to the whole worker process group.
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python", "-m", "hermes_zalo.container_runtime"]
CMD ["gateway", "run"]
