"""Explicit, opt-in configuration for the private administration surface."""
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AdminConfig:
    enabled: bool = False
    conversations: bool = False
    retention_days: int = 30
    host: str = '127.0.0.1'
    port: int = 8787
    public_url: str = ''
    proxy_secret: str = field(default='', repr=False)

    @classmethod
    def from_mapping(cls, values):
        def flag(key):
            raw = str(values.get(key) or 'false').strip().lower()
            if raw not in ('true', 'false'):
                raise ValueError(key + ' cần là true hoặc false.')
            return raw == 'true'
        def integer(key, default, low, high):
            raw = str(values.get(key) or default)
            if not raw.isascii() or not raw.isdigit() or not low <= int(raw) <= high:
                raise ValueError(key + ' nằm ngoài giới hạn cho phép.')
            return int(raw)
        enabled = flag('ZALO_ADMIN_ENABLED')
        conversations = flag('ZALO_CONVERSATIONS_ENABLED')
        retention = integer('ZALO_CONVERSATION_RETENTION_DAYS', 30, 1, 365)
        host = str(values.get('ZALO_ADMIN_HOST') or '127.0.0.1').strip()
        port = integer('ZALO_ADMIN_PORT', values.get('PORT') or 8787, 1, 65535)
        public = str(values.get('ZALO_ADMIN_PUBLIC_URL') or '').rstrip('/')
        proxy_secret = str(values.get('ZALO_ADMIN_PROXY_SECRET') or '')
        if proxy_secret and (not 32 <= len(proxy_secret) <= 256 or not proxy_secret.isascii()
                or any(not 33 <= ord(char) <= 126 for char in proxy_secret)):
            raise ValueError('ZALO_ADMIN_PROXY_SECRET cần 32–256 ký tự ASCII, không có khoảng trắng.')
        if host not in ('127.0.0.1', '0.0.0.0'):
            raise ValueError('ZALO_ADMIN_HOST chỉ hỗ trợ 127.0.0.1 hoặc 0.0.0.0.')
        if enabled and not conversations:
            raise ValueError('Bật ZALO_CONVERSATIONS_ENABLED trước khi bật trang Admin.')
        if public:
            parsed = urlsplit(public)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query
                    or parsed.fragment or parsed.username or parsed.password):
                raise ValueError('ZALO_ADMIN_PUBLIC_URL cần là gốc địa chỉ HTTPS, không kèm đường dẫn.')
        if enabled and host == '0.0.0.0' and not public:
            raise ValueError('Lắng nghe ngoài máy cần ZALO_ADMIN_PUBLIC_URL và proxy HTTPS.')
        return cls(enabled, conversations, retention, host, port, public, proxy_secret)
