"""Avoid token-bearing HTTP URLs in logs, including dependency logs."""

import logging
import os
import re
import traceback


class SecretFilter(logging.Filter):
    @staticmethod
    def redact(message: str) -> str:
        for key, value in os.environ.items():
            if any(word in key.upper() for word in ("TOKEN", "API_KEY", "SECRET")) and len(value) >= 6:
                message = message.replace(value, "[REDACTED]")
        return re.sub(r"/bot[^/\s]+/", "/bot[REDACTED]/", message)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = self.redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redact(record.stack_info)
        return True


def protect_logs() -> None:
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, SecretFilter) for f in handler.filters):
            handler.addFilter(SecretFilter())
