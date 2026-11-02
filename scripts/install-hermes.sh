#!/usr/bin/env bash
# Cài Hermes Agent ở commit đã ghim vào .runtime/ cho Linux/macOS.
# Tương đương scripts/install-hermes.ps1 nhưng KHÔNG chạy installer chính thức
# (installer đó ghi lệnh `hermes` vào ~/.local/bin và sửa ~/.bashrc, có thể đè
# bản Hermes cá nhân trong ~/.hermes). Mọi thứ nằm trong .runtime/:
#   .runtime/hermes-agent        mã nguồn Hermes ở commit ghim
#   .runtime/hermes-agent/venv   môi trường Python 3.14 (uv) với extra `all`
#   .runtime/hermes-home/bin/hermes  launcher (symlink tới venv/bin/hermes)
#
# Biến tùy chọn:
#   HERMES_LOCAL_SOURCE  bản git Hermes có sẵn để clone --shared, không tải lại
#                        (mặc định ~/.hermes/hermes-agent; chỉ đọc, không sửa)
#   HERMES_PYTHON_VERSION  mặc định 3.14 (khớp Dockerfile)
set -euo pipefail

# Giữ đồng bộ với Dockerfile và scripts/install-hermes.ps1.
COMMIT=93c9360a8da592cee43e8895403e91c7a920b0ce
REPO_URL=https://github.com/NousResearch/hermes-agent.git

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"
SRC="$RUNTIME/hermes-agent"
HOME_DIR="$RUNTIME/hermes-home"
LOCAL_SOURCE="${HERMES_LOCAL_SOURCE:-$HOME/.hermes/hermes-agent}"
PY_VERSION="${HERMES_PYTHON_VERSION:-3.14}"
export UV_NO_CONFIG=1

log() { printf '==> %s\n' "$*"; }
die() { printf 'Lỗi: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null || die "cần git."
command -v uv >/dev/null || die "cần uv (https://docs.astral.sh/uv/): curl -LsSf https://astral.sh/uv/install.sh | sh"
mkdir -p "$RUNTIME"

# 1. Mã nguồn ở đúng commit ghim.
fresh=false
if [ ! -d "$SRC/.git" ]; then
    fresh=true
    [ -e "$SRC" ] && die "$SRC đã tồn tại nhưng không phải git checkout; hãy dời nó đi."
    if git -C "$LOCAL_SOURCE" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
        log "Clone --shared từ $LOCAL_SOURCE (không tải lại từ mạng)"
        git clone -q --shared --no-checkout "$LOCAL_SOURCE" "$SRC"
        git -C "$SRC" remote set-url origin "$REPO_URL"
        # Objects dùng chung qua .git/objects/info/alternates: giữ nguyên bản nguồn.
    else
        log "Tải commit $COMMIT từ $REPO_URL"
        git init -q "$SRC"
        git -C "$SRC" remote add origin "$REPO_URL"
        git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
    fi
fi
if [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)" != "$COMMIT" ]; then
    if [ "$fresh" = false ] && [ -n "$(git -C "$SRC" status --porcelain --untracked-files=no 2>/dev/null || true)" ]; then
        die "$SRC có thay đổi cục bộ; không checkout đè."
    fi
    git -C "$SRC" cat-file -e "$COMMIT^{commit}" 2>/dev/null \
        || git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
    git -C "$SRC" checkout -q --detach "$COMMIT"
fi
[ "$(git -C "$SRC" rev-parse HEAD)" = "$COMMIT" ] || die "không checkout được $COMMIT"
log "Hermes source: $SRC @ ${COMMIT:0:12}"

# 2. Môi trường phụ thuộc (pin chính xác trong pyproject của Hermes).
if [ ! -x "$SRC/venv/bin/python" ]; then
    log "Tạo venv Python $PY_VERSION"
    uv venv -q --python "$PY_VERSION" "$SRC/venv"
fi
log "Cài Hermes + extra all (gồm google-auth, httpx, ruamel.yaml)"
(cd "$SRC" && uv pip install -q --python venv/bin/python -e '.[all]')

# 3. Launcher trong HERMES_HOME riêng của dự án (không dùng ~/.hermes).
mkdir -p "$HOME_DIR/bin"
ln -sfn "$SRC/venv/bin/hermes" "$HOME_DIR/bin/hermes"

# 4. Kiểm tra giống Dockerfile.
"$SRC/venv/bin/python" -c 'import hermes_bootstrap, google.auth, httpx; from ruamel.yaml import YAML; from gateway.platforms.base import BasePlatformAdapter; from gateway.config import PlatformConfig'
HERMES_HOME="$HOME_DIR" "$HOME_DIR/bin/hermes" --version | head -1
log "Xong. Tiếp theo: scripts/run.sh Setup, rồi scripts/run.sh TestHermes"
