"""Read-only Workload preflight; never enroll users or create a database."""
import argparse
import asyncio
from collections import Counter
import os
from pathlib import Path
import sys

from hermes_zalo.config import ConfigError
from hermes_zalo.sheets import SheetsClient, SheetsError, SheetsSettings
from hermes_zalo.workload import CONFIG_ERROR, WorkloadDirectory, WorkloadError, WorkloadSettings, _name_key


ROOT = Path(__file__).resolve().parent.parent


def local_values(env_file=None, environ=None):
    """Use the launcher's literal env syntax without changing process env."""
    values = dict(os.environ if environ is None else environ)
    path = ROOT / '.env' if env_file is None else Path(env_file)
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not separator or not key.replace('_', '').isalnum():
                raise ConfigError('Cấu hình .env cần có dạng KEY=value.')
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values.setdefault(key, value)
    return values


async def check(values):
    # This command needs only the source, not bot/admin/registration settings.
    # Force readonly OAuth in this local config even if issues are enabled.
    source = {key: values.get(key, '') for key in ('ZALO_SHEETS_ID', 'ZALO_GOOGLE_CREDENTIALS')}
    credentials = source['ZALO_GOOGLE_CREDENTIALS'].strip()
    if credentials:
        source['ZALO_GOOGLE_CREDENTIALS'] = str((ROOT / credentials).resolve())
    source['ZALO_ISSUES_ENABLED'] = 'false'
    sheets = SheetsSettings.from_mapping(source)
    if sheets is None:
        raise WorkloadError(CONFIG_ERROR)
    configuration = dict(values)
    configuration['REGISTRATION_ENABLED'] = 'true'
    directory_settings = WorkloadSettings.from_mapping(configuration, sheets)
    client = SheetsClient(sheets)
    try:
        rows = await WorkloadDirectory(directory_settings, client)._read()
        phone_counts = Counter(phone for _, phone in rows if phone is not None)
        name_counts = Counter(key for name, _ in rows if (key := _name_key(name)))
        result = {
            'rows': len(rows),
            'valid_phone_rows': sum(phone is not None for _, phone in rows),
            'invalid_phone_rows': sum(phone is None for _, phone in rows),
            'duplicate_phone_groups': sum(count > 1 for count in phone_counts.values()),
            'duplicate_name_groups': sum(count > 1 for count in name_counts.values()),
        }
    finally:
        await client.close()
    print('Đọc được Workload; cột và tiêu đề khớp cấu hình.')
    print(f"Dòng có dữ liệu: {result['rows']}.")
    print(f"Số điện thoại hợp lệ: {result['valid_phone_rows']}; thiếu hoặc sai: {result['invalid_phone_rows']}.")
    print(f"Nhóm số điện thoại trùng: {result['duplicate_phone_groups']}.")
    print(f"Nhóm tên trùng (không phân biệt hoa/thường và dấu): {result['duplicate_name_groups']}.")
    print('Chỉ kiểm tra dữ liệu nguồn; chưa bật đăng ký, chưa xác minh người dùng hoặc kiểm tra luồng Zalo.')
    return result


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Kiểm tra Workload chỉ đọc, không in dữ liệu cá nhân.')
    parser.add_argument('action', choices=('check',))
    parser.parse_args(argv)
    try:
        asyncio.run(check(local_values()))
    except (ConfigError, SheetsError, WorkloadError) as error:
        print('Kiểm tra chưa thành công. ' + str(error))
        return 1
    except Exception:
        print('Kiểm tra chưa thành công. Kiểm tra cấu hình và kết nối; thông tin riêng không được in ra.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
