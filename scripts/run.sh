#!/usr/bin/env bash
# Linux/macOS tương đương scripts/run.ps1.
#   scripts/run.sh [Lệnh]   (mặc định: Check)
# Venv dự án: .runtime/venv. Mã Hermes: HERMES_SRC (mặc định bản cài trên máy
# ~/.hermes/hermes-agent; bản ghim giống cloud: scripts/install-hermes.sh rồi
# HERMES_SRC=.runtime/hermes-agent). HERMES_HOME luôn là .runtime/hermes-home,
# không bao giờ ~/.hermes. Log: .runtime/logs/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ACTION="${1:-Check}"
ACTIONS="Status Setup SetupSheets SheetsCheck RegistrationCheck Update PrepareDocs PrepareSheets FeatureDemo Report Check Demo Test TestHermes NluCheck NluCheckLive TicketsDemo TicketsTest TicketsChatDemo TicketsLabCheck TicketsLinkUsers ZaloCheck Discover Model Start AdminDemo AdminCreate Console ConsoleAccount"
case " $ACTIONS " in
    *" $ACTION "*) ;;
    *) echo "Lệnh không hợp lệ: $ACTION" >&2; echo "Hỗ trợ: $ACTIONS" >&2; exit 2 ;;
esac

export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
VENV="$ROOT/.runtime/venv"
PY="$VENV/bin/python"
HERMES_SRC="${HERMES_SRC:-$HOME/.hermes/hermes-agent}"
case "$HERMES_SRC" in /*) ;; *) HERMES_SRC="$ROOT/$HERMES_SRC" ;; esac
export HERMES_SRC
HERMES_HOME_DIR="$ROOT/.runtime/hermes-home"
LOGS="$ROOT/.runtime/logs"
mkdir -p "$LOGS"

# Chạy module Python bên trong runtime Hermes đã chọn.
#  - Bản cài chính thức có launcher .hermes/bin/hermes (hỗ trợ --run-module).
#  - Bản dựng bằng install-hermes.sh có venv/bin/python.
hermes_module() {
    if [ -x "$HERMES_SRC/.hermes/bin/hermes" ]; then
        HERMES_HOME="$HERMES_HOME_DIR" "$HERMES_SRC/.hermes/bin/hermes" --run-module "$@"
    elif [ -x "$HERMES_SRC/venv/bin/python" ]; then
        HERMES_HOME="$HERMES_HOME_DIR" PYTHONPATH="$ROOT/src" "$HERMES_SRC/venv/bin/python" -m "$@"
    else
        echo "Không tìm thấy Hermes ở $HERMES_SRC (cần .hermes/bin/hermes hoặc venv/bin/python)." >&2
        return 1
    fi
}

# Chạy lệnh test, ghi log đầy đủ và một dòng tóm tắt vào .runtime/logs/last-tests.txt.
run_tests() {
    local name="$1"; shift
    local log="$LOGS/$name.log" rc summary
    set +e
    "$@" 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e
    summary="$(grep -E '^Ran [0-9]+ tests' "$log" | tail -1) / $(grep -E '^(OK|FAILED)' "$log" | tail -1)"
    touch "$LOGS/last-tests.txt"
    grep -v "^$name " "$LOGS/last-tests.txt" > "$LOGS/last-tests.tmp" || true
    printf '%s %s | %s | exit=%s | hermes=%s\n' "$name" "$(date '+%F %T')" "$summary" "$rc" "$(hermes_rev)" >> "$LOGS/last-tests.tmp"
    mv "$LOGS/last-tests.tmp" "$LOGS/last-tests.txt"
    return "$rc"
}

hermes_rev() { git -C "$HERMES_SRC" log -1 --format='%h %cd' --date=short 2>/dev/null || echo '?'; }

if [ "$ACTION" = Status ]; then
    ok() { printf '  %s: %s\n' "$1" "$2"; }
    echo "== Hermes"
    ok "Mã nguồn (HERMES_SRC)" "$HERMES_SRC @ $(hermes_rev)"
    ok "HERMES_HOME (test/bot)" "$HERMES_HOME_DIR"
    [ -d "$ROOT/.runtime/hermes-agent" ] && ok "Bản ghim cloud" "$ROOT/.runtime/hermes-agent (có cài)"
    echo "== Plugin Zalo"
    pl="$HERMES_HOME_DIR/plugins/zalo-platform"
    if [ -d "$pl" ]; then ok "Đã cài" "$pl ($(date -r "$pl" '+%F %T'))"; else ok "Chưa cài" "chạy: scripts/run.sh Setup"; fi
    if [ -d "$HOME/.hermes/plugins/zalo-platform" ]; then ok "~/.hermes (thật)" "CÓ plugin Zalo"; else ok "~/.hermes (thật)" "chưa cài plugin Zalo"; fi
    [ -f "$ROOT/.env" ] && ok ".env" "có" || ok ".env" "chưa có (cần token để Start)"
    echo "== Tiến trình"
    ok "Gateway thật" "$(systemctl --user is-active hermes-gateway.service 2>/dev/null || echo '?') (hermes-gateway.service)"
    if [ -f "$LOGS/gateway.pid" ] && kill -0 "$(cat "$LOGS/gateway.pid")" 2>/dev/null; then
        ok "Gateway của repo" "đang chạy (pid $(cat "$LOGS/gateway.pid")), log: $LOGS/gateway.log"
    else ok "Gateway của repo" "không chạy"; fi
    echo "== Test gần nhất ($LOGS/last-tests.txt)"
    if [ -s "$LOGS/last-tests.txt" ]; then sed 's/^/  /' "$LOGS/last-tests.txt"; else echo "  chưa có"; fi
    exit 0
fi

if [ "$ACTION" = Setup ] || [ "$ACTION" = SetupSheets ]; then
    if [ ! -x "$PY" ]; then
        mkdir -p "$ROOT/.runtime"
        "${PYTHON:-python3}" -m venv "$VENV"
    fi
    if [ "$ACTION" = SetupSheets ]; then pkg='.[sheets,backup]'; else pkg='.'; fi
    "$PY" -m pip install -q -e "$pkg"
    if [ "$ACTION" = SetupSheets ]; then
        # install-hermes.sh cài Hermes với extra `all`, đã gồm google-auth.
        # google-auth phải có trong runtime Hermes (bản cài đầy đủ và bản ghim `.[all]` đều có).
        hermes_module site >/dev/null 2>&1 || { echo "Không chạy được Hermes ở $HERMES_SRC." >&2; exit 1; }
    fi
    exec "$PY" scripts/configure.py
fi

[ -x "$PY" ] || { echo "Chạy scripts/run.sh Setup trước." >&2; exit 1; }

case "$ACTION" in
    AdminDemo)        exec "$PY" -m hermes_zalo.admin_cli demo ;;
    AdminCreate)      exec "$PY" -m hermes_zalo.admin_cli create-account ;;
    # Agent Console thật (không demo): lần đầu hỏi tài khoản chủ sở hữu rồi mở
    # http://127.0.0.1:8787/. Từ chối chạy nếu còn tài khoản dùng mật khẩu demo.
    # Dữ liệu: .runtime/hermes-home (đổi bằng --home). Kiểm tra: curl http://127.0.0.1:8787/healthz
    # Tham số thêm được chuyển tiếp, ví dụ: scripts/run.sh Console --port 8790
    Console)          shift; HERMES_HOME="$HERMES_HOME_DIR" exec "$PY" -m hermes_zalo.admin_cli start "$@" ;;
    ConsoleAccount)   shift; HERMES_HOME="$HERMES_HOME_DIR" exec "$PY" -m hermes_zalo.admin_cli create-console-account "$@" ;;
    Update)           exec "$PY" scripts/configure.py ;;
    PrepareDocs)      exec "$PY" scripts/features.py prepare-docs ;;
    PrepareSheets)    exec "$PY" scripts/features.py prepare-sheets ;;
    SheetsCheck)      exec "$PY" scripts/features.py sheets-check ;;
    RegistrationCheck) exec "$PY" scripts/registration.py check ;;
    NluCheck|NluCheckLive)
        live=(); [ "$ACTION" = NluCheckLive ] && live=(--live)
        if [ -x "$HERMES_SRC/venv/bin/python" ] && [ ! -x "$HERMES_SRC/.hermes/bin/hermes" ]; then
            # Bản install-hermes.sh: như Docker, chạy thẳng Python của venv Hermes.
            HERMES_HOME="$HERMES_HOME_DIR" exec "$HERMES_SRC/venv/bin/python" scripts/nlu_check.py --runtime "${live[@]}"
        fi
        exec "$PY" scripts/nlu_check.py "${live[@]}" ;;
    FeatureDemo)      exec "$PY" scripts/features.py demo ;;
    Report)           exec "$PY" scripts/features.py report ;;
    Check)            exec "$PY" -m hermes_zalo.cli check ;;
    Demo)             exec "$PY" -m hermes_zalo.cli demo ;;
    Test)
        export PYTHONPATH="$ROOT/src"
        run_tests Test "$PY" -m unittest discover -s tests ;;
    TestHermes)
        # Như run.ps1: chạy unittest bên trong runtime Hermes (không gọi mạng/model).
        export HERMES_INTEGRATION_TESTS=1
        run_tests TestHermes hermes_module unittest discover -s tests -p "test_hermes_*.py" -v ;;
    ZaloCheck)        exec "$PY" -m hermes_zalo.cli probe ;;
    Discover)         exec "$PY" -m hermes_zalo.cli discover ;;
    TicketsDemo)      exec "$PY" scripts/launch.py tickets demo ;;
    TicketsTest)      exec "$PY" scripts/launch.py tickets sheets_test ;;
    TicketsChatDemo)  exec "$PY" -m hermes_zalo.tickets.chat_demo ;;
    TicketsLabCheck)  exec "$PY" -m hermes_zalo.tickets.cli check-lab ;;
    TicketsLinkUsers) exec "$PY" -m hermes_zalo.tickets.cli import-zalo-users --mode lab_live ;;
    Model)            exec "$PY" scripts/launch.py model ;;
    Start)
        # Chạy foreground, đồng thời ghi log để theo dõi: tail -f .runtime/logs/gateway.log
        echo $$ > "$LOGS/gateway.pid"
        trap 'rm -f "$LOGS/gateway.pid"' EXIT
        echo "Log: $LOGS/gateway.log  (Hermes: $HERMES_SRC @ $(hermes_rev))"
        "$PY" scripts/launch.py gateway run 2>&1 | tee -a "$LOGS/gateway.log"
        exit "${PIPESTATUS[0]}" ;;
esac
