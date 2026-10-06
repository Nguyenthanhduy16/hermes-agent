"""Local checks; diagnostics never send messages or make model calls."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

from .config import ConfigError, Settings, load_env
from .events import is_allowed, parse_update, split_text
from .logging_utils import protect_logs
from .state import StateStore
from .api import WebhookInspectionUnavailable, ZaloAPIError


def demo() -> None:
    """Synthetic input/output through the admission rules, no Hermes or API."""
    with tempfile.TemporaryDirectory() as directory:
        store = StateStore(Path(directory) / "state.sqlite")
        handled = []
        for user, mid in [("demo-user", "1"), ("demo-user", "1"), ("stranger", "2")]:
            payload = {"event_name": "message.text.received", "message": {
                "from": {"id": user, "is_bot": False},
                "chat": {"id": "demo-group", "chat_type": "GROUP"},
                "message_id": mid, "text": "Xin chào",
            }}
            message = parse_update(payload)
            if is_allowed(message, frozenset(["demo-user"]), frozenset(["demo-group"])) and store.reserve(message.key):
                handled.append({"chat_id": message.chat_id, "session": message.session_key,
                                "text": "[MÔ PHỎNG] Đã nhận: " + message.text})
                store.finish(message.key, "demo")
        store.close()
        assert len(handled) == 1
        print(json.dumps({"mode": "offline_simulation", "model_called": False, "zalo_called": False,
                          "delivered": handled, "duplicate_blocked": True, "unauthorized_blocked": True},
                         ensure_ascii=False, indent=2))


async def online_check(command: str, seconds: int) -> int:
    from .api import ZaloClient
    token = os.getenv("ZALO_BOT_TOKEN", "").strip()
    # Discovery intentionally requires a token but not an existing allowlist.
    if not token or token.startswith("YOUR_") or any(c in token for c in "/?#\\ \r\n"):
        raise ConfigError("Can dien ZALO_BOT_TOKEN hop le trong .env.")
    client = ZaloClient(token)
    try:
        me = await client.get_me()
        print("Token: hợp lệ. Quyền vào nhóm:", me.get("can_join_groups", "chưa rõ"))
        webhook_verified = True
        try:
            webhook = await client.get_webhook_info()
        except WebhookInspectionUnavailable:
            webhook_verified = False
            webhook = None
            print("Zalo trả mã 404 khi đọc webhook: chưa xác minh được trạng thái webhook.")
            print("Có thể tiếp tục thử long polling; không thay đổi hoặc xóa webhook.")
        if webhook is not None and webhook.get("url"):
            print("Bot đang có webhook. Dừng; hãy gỡ webhook trước khi dùng polling.")
            return 2
        if command == "probe":
            if webhook_verified:
                print("Webhook: chưa được cấu hình. Có thể thử long polling.")
            print("Chưa gọi Hermes, chưa gửi tin. Bước tiếp theo: chạy Discover và nhắn cho bot.")
            return 0
        print("Chỉ ghi ID người gửi/nhóm, không ghi nội dung, không trả lời, không gọi AI.")
        print("Hãy gửi tin riêng cho bot; hoặc @bot trong nhóm. Dừng gateway trước bước này.")
        deadline = asyncio.get_running_loop().time() + seconds
        seen = set()
        while asyncio.get_running_loop().time() < deadline:
            timeout = max(1, min(10, int(deadline - asyncio.get_running_loop().time())))
            update = await client.poll(timeout=timeout)
            message = parse_update(update)
            if message:
                identity = (message.user_id, message.chat_id, message.chat_type)
                if identity not in seen:
                    seen.add(identity)
                    print(json.dumps({"user_id": message.user_id, "chat_id": message.chat_id,
                                      "chat_type": message.chat_type}, ensure_ascii=False))
        if not seen:
            print("Chưa nhận được tin hợp lệ trong khoảng chờ.")
        return 0
    finally:
        await client.close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Kiểm tra adapter Zalo–Hermes")
    parser.add_argument("command", choices=("check", "demo", "probe", "discover"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.command == "demo":
            demo()
            return 0
        load_env(args.env_file)
        protect_logs()
        if args.command == "check":
            config = Settings.from_env()
            print(f"Cấu hình hợp lệ: {len(config.allowed_users)} người, {len(config.allowed_groups)} nhóm.")
            print("Quyền nhóm: mọi thành viên được đọc nội dung đã chia sẻ." if config.group_access == "members"
                  else "Quyền nhóm: chỉ người có trong danh sách được cấp quyền.")
            print("Chỉ kiểm tra cấu hình, chưa xác thực Zalo hoặc model.")
            return 0
        if not 1 <= args.seconds <= 300:
            raise ConfigError("--seconds phai tu 1 den 300.")
        return asyncio.run(online_check(args.command, args.seconds))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ZaloAPIError as exc:
        # Only operation names and numeric codes are displayed. Never show a
        # raw API description, request URL, response body or arbitrary message.
        known_methods = {"getMe", "getWebhookInfo", "getUpdates", "sendMessage"}
        operation = exc.method if exc.method in known_methods else "API Zalo"
        codes = []
        if type(exc.status_code) is int:
            codes.append(f"HTTP {exc.status_code}")
        if type(exc.code) is int:
            codes.append(f"mã Zalo {exc.code}")
        detail = "; ".join(codes) if codes else "không xác nhận được phản hồi API"
        print(f"Kiểm tra thất bại tại {operation} ({detail}).", file=sys.stderr)
        if exc.code == 401:
            print("Zalo từ chối token. Kiểm tra token đã lưu trên máy.", file=sys.stderr)
        elif exc.code == 429:
            print("Zalo đang giới hạn yêu cầu. Kiểm tra hạn mức rồi thử lại sau.", file=sys.stderr)
        else:
            print("Kiểm tra mạng hoặc trạng thái dịch vụ Zalo; token có thể vẫn hợp lệ.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Đã dừng.")
        return 130
    except Exception as exc:
        # Never print an arbitrary exception, traceback, HTTP URL or response body.
        print(f"Kiểm tra thất bại ({type(exc).__name__}). Kiểm tra token, mạng và hạn mức.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
