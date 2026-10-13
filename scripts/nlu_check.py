"""Check configured Hermes NLU; --live sends one synthetic question only."""
import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--runtime', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.runtime:
        hermes = ROOT / '.runtime/hermes-agent'
        if not (hermes / 'hermes_cli/_launchers.py').is_file():
            print('Thiếu Hermes native. Trong container dùng: /opt/hermes/venv/bin/python /app/scripts/nlu_check.py --runtime')
            return 2
        values = os.environ.copy()
        values['HERMES_HOME'] = str(ROOT / '.runtime/hermes-home')
        # Use Hermes' own installation-bound interpreter/bootstrap, preserving
        # Unicode Windows paths. Its isolated launcher cannot import repo scripts.
        sys.path.insert(0, str(hermes))
        from hermes_cli._launchers import runtime_command
        command = runtime_command(hermes, code="runpy.run_path(sys.argv.pop(1), run_name='__main__')",
                                  args=[str(Path(__file__).resolve()), '--runtime'] + (['--live'] if args.live else []))
        result = subprocess.call(command, cwd=ROOT, env=values)
        if result:
            print('Kiểm tra chưa đạt hoặc chưa truy cập được runtime Hermes; bot đang chạy không bị thay đổi.')
        return result
    import hermes_bootstrap  # Activate the sealed runtime too when invoked directly in Docker.
    from hermes_zalo.config import load_env
    from hermes_zalo.logging_utils import protect_logs
    from hermes_zalo.nlu import IntentError, IntentRouter, configured_endpoint, options, to_action
    from hermes_constants import get_hermes_home
    load_env(ROOT / '.env')
    load_env(Path(get_hermes_home()) / '.env')
    protect_logs()
    try:
        enabled, timeout = options(os.environ)
        endpoint = configured_endpoint()
        print(f'Provider: {endpoint.provider}; model: {endpoint.model}; NLU bật: {enabled}.')
        if not args.live:
            print('Cấu hình đọc được. Chưa gọi model; cần NluCheckLive để kiểm tra phản hồi có cấu trúc.')
            return 0
        async def probe():
            router = IntentRouter(timeout=timeout)
            try:
                question = 'Giúp kiểm tra lịch của mình ngày kế tiếp nhé'
                data = await router.classify(question, allowed={'calendar', 'clarify'}, user_key='synthetic-probe')
                action = to_action(data, question)
                return action.command == '/lichcuatoi' and action.argument == 'mai'
            finally:
                await router.close()
        if not asyncio.run(probe()):
            print('Model đã trả lời nhưng chưa nhận diện đúng câu mẫu. Chưa xác nhận đạt.')
            return 1
        print('Đạt: model hiện tại trả đúng ý định và dữ liệu có cấu trúc cho 1 câu mẫu.')
        print('Không nhắn Zalo, không đọc/ghi Sheets. Đây chưa phải thử nghiệm Zalo thật.')
        return 0
    except IntentError as error:
        reasons = {'unsupported': 'Model/endpoint hiện tại chưa đáp ứng yêu cầu JSON Schema.',
                   'credentials': 'Nhà cung cấp từ chối thông tin xác thực.',
                   'quota': 'Tài khoản nhà cung cấp không đủ hạn mức.',
                   'forbidden': 'Nhà cung cấp từ chối truy cập.',
                   'rate_limited': 'Đã chạm giới hạn lượt gọi model; thử lại sau.',
                   'timeout': 'Model chưa trả lời trong thời gian cho phép.',
                   'invalid': 'Model trả dữ liệu sai cấu trúc.',
                   'ungrounded': 'Model thêm thông tin không có trong câu mẫu.',
                   'provider': 'Lỗi kết nối hoặc phản hồi từ nhà cung cấp.',
                   'configuration': 'Cấu hình endpoint/key chưa hợp lệ.'}
        print('Chưa đạt. ' + reasons.get(str(error), 'Không nhận được ý định hợp lệ.'))
        return 1
    except Exception:
        print('Chưa đạt. Kiểm tra model có hỗ trợ JSON Schema, API key, mạng và hạn mức. Không đổi model tự động.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
