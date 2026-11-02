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
                raise ValueError(key + ' must be true or false.')
            return raw == 'true'
        def integer(key, default, low, high):
            raw = str(values.get(key) or default)
            if not raw.isascii() or not raw.isdigit() or not low <= int(raw) <= high:
                raise ValueError(key + ' is outside the allowed range.')
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
            raise ValueError('ZALO_ADMIN_PROXY_SECRET must contain 32 to 256 ASCII characters without whitespace.')
        if host not in ('127.0.0.1', '0.0.0.0'):
            raise ValueError('ZALO_ADMIN_HOST only supports 127.0.0.1 or 0.0.0.0.')
        if enabled and not conversations:
            raise ValueError('Enable ZALO_CONVERSATIONS_ENABLED before enabling the administration console.')
        if public:
            parsed = urlsplit(public)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query
                    or parsed.fragment or parsed.username or parsed.password):
                raise ValueError('ZALO_ADMIN_PUBLIC_URL must be an HTTPS origin without a path.')
        if enabled and host == '0.0.0.0' and not public:
            raise ValueError('Listening beyond localhost requires ZALO_ADMIN_PUBLIC_URL and an HTTPS proxy.')
        return cls(enabled, conversations, retention, host, port, public, proxy_secret)
