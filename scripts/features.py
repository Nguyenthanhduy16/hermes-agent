"""Offline setup and acceptance helpers; no Zalo or model calls."""
import argparse
import asyncio
import csv
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from hermes_zalo.config import Settings, load_env
from hermes_zalo.events import IncomingMessage
from hermes_zalo.knowledge import KnowledgeBase
from hermes_zalo.pilot import PilotStore, format_report
from hermes_zalo.reminders import VIETNAM_TZ
from hermes_zalo.sheets import CALENDAR_HEADER, ISSUE_HEADER, SheetsClient, SheetsSettings, SheetsError
from hermes_zalo.work import WorkService, state_prefix

ROOT = Path(__file__).resolve().parent.parent


def local_settings():
    load_env(ROOT / ".env")
    # Match scripts/launch.py so helpers and the bot address the same databases.
    values = dict(os.environ)
    values["ZALO_STATE_DIR"] = str(ROOT / ".runtime/hermes-home/data/zalo")
    return Settings.from_mapping(values)


def prepare_docs(settings):
    target = settings.knowledge_dir
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / "manifest.json"
    if manifest.exists():
        print("Đã có manifest tài liệu; giữ nguyên dữ liệu và quyền hiện tại.")
        return
    sample = ROOT / "examples/knowledge"
    source = sample / "so-tay-mau.md"
    destination = target / source.name
    if destination.exists():
        raise ValueError("Đã có file cùng tên mẫu; giữ nguyên file. Hãy tự tạo manifest theo hướng dẫn.")
    shutil.copy2(source, destination)
    data = json.loads((sample / "manifest.json").read_text(encoding="utf-8"))
    for entry in data["documents"]:
        entry["users"] = sorted(settings.allowed_users)
        entry["groups"] = []
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Đã tạo tài liệu MẪU và cấp quyền nhắn riêng cho danh sách hiện có. Chưa cấp quyền nhóm.")
    print("Thử trên Zalo: /hoi Giờ làm việc của nhóm là khi nào?")


def prepare_sheets(settings):
    directory = ROOT / "data/sheets-templates"
    directory.mkdir(parents=True, exist_ok=True)
    tomorrow = datetime.now(VIETNAM_TZ).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    rows = {
        "Lich.csv": [CALENDAR_HEADER, ["demo-01", tomorrow.isoformat(), (tomorrow + timedelta(minutes=30)).isoformat(),
                      "LỊCH MẪU: họp thử bot", ",".join(sorted(settings.allowed_users)), "", "Trực tuyến"]],
        "Issues.csv": [ISSUE_HEADER],
    }
    for name, values in rows.items():
        path = directory / name
        if path.exists():
            print(f"Giữ nguyên mẫu đã có: {name}")
            continue
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows(values)
    print("Mẫu nằm trong data/sheets-templates. Nhập Lich.csv và Issues.csv vào hai tab Lich và Issues.")
    print("Dòng lịch là dữ liệu giả cho ngày mai. Đặt cột start/end thành Văn bản thuần túy để giữ +07:00.")


async def demo():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shutil.copytree(ROOT / "examples/knowledge", root / "knowledge")
        settings = Settings("offline-demo-token", frozenset({"u_demo"}), state_dir=root, knowledge_dir=root / "knowledge")
        service = WorkService(settings, environ={})
        try:
            print("MÔ PHỎNG NGOẠI TUYẾN — không gọi Zalo, Google Sheets hay AI.")
            for mid, text in enumerate(("/hoi Giờ làm việc của nhóm là khi nào?", "/hoi Ngân sách mua máy bay là bao nhiêu?",
                                       "/nhac sau 1 phut | Kiểm tra nhắc việc", "/nhac xem")):
                message = IncomingMessage("dm_demo", "u_demo", str(mid), "PRIVATE", text)
                print("\n" + text + "\n" + await service.handle(message))
            reminder = service.reminders.list_for("u_demo", "dm_demo", "PRIVATE")[0]
            assert service.reminders.cancel(reminder.id, "u_demo", "dm_demo", "PRIVATE")
            assert not service.reminders.claim_due(now=reminder.due_at + 1)
            print("\nĐã kiểm tra hủy nhắc; không có lần gửi thật nào.")
        finally:
            await service.close()


async def sheets_check():
    config = SheetsSettings.from_mapping(os.environ)
    if config is None:
        raise ValueError("Chưa có cấu hình Sheets.")
    client = SheetsClient(config)
    try:
        result = await client.check()
        if config.calendar_format == "lab":
            print(f"Đọc được {len(config.calendar_tabs)} tab Lịch lab: {result['calendar_rows']} mục lịch.")
        else:
            print(f"Đọc được đúng mẫu: {result['calendar_rows']} dòng lịch.")
        print("Issue đang tắt." if result['issue_rows'] is None else f"Đọc được {result['issue_rows']} dòng issue.")
        print("Chỉ kiểm tra quyền đọc và cấu trúc; chưa kiểm tra quyền ghi, chưa tạo issue.")
    finally:
        await client.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare-docs", "prepare-sheets", "sheets-check", "demo", "report"))
    action = parser.parse_args().action
    if action == "demo":
        asyncio.run(demo())
        return
    settings = local_settings()
    if action == "prepare-docs":
        prepare_docs(settings)
    elif action == "prepare-sheets":
        prepare_sheets(settings)
    elif action == "sheets-check":
        asyncio.run(sheets_check())
    else:
        pilot = PilotStore(state_prefix(settings).with_suffix(".pilot.sqlite3"))
        try:
            report = format_report(pilot.report(ROOT / ".runtime/hermes-home/state.db"))
            folder = ROOT / "data/reports"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "pilot-latest.txt").write_text(report, encoding="utf-8")
            print(report)
            print("\nĐã lưu data/reports/pilot-latest.txt (không đưa lên Git).")
        finally:
            pilot.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Chưa thực hiện được. Kiểm tra cấu hình và dữ liệu theo README; thông tin bí mật không được in ra.")
        raise SystemExit(1)
