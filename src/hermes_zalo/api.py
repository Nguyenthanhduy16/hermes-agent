"""Small async client for the official Zalo Bot API.

getUpdates yields one event, without Telegram offsets. Read requests retry;
an ambiguous send never retries because Zalo documents no idempotency key.
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random

import httpx


class ZaloAPIError(RuntimeError):
    """Safe to display: deliberately excludes response content and request URLs."""

    def __init__(
        self,
        message: str = "Zalo API request failed.",
        *,
        code: int | None = None,
        retry_after: float | None = None,
        status_code: int | None = None,
        method: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after
        self.status_code = status_code
        self.method = method


class AuthenticationError(ZaloAPIError):
    """Invalid credentials are fatal; retrying cannot repair a token."""


class DeliveryUncertain(ZaloAPIError):
    """The server may have accepted a send; automatic resend risks duplicates."""


class WebhookInspectionUnavailable(ZaloAPIError):
    """A 404 inspecting webhook state is unknown, not proof no webhook exists."""


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(60.0, max(0.0, seconds)) if math.isfinite(seconds) else None


def _error_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        # Do not preserve arbitrary text from an untrusted response as a code.
        return int(value) if len(value) <= 6 else None
    return None


class ZaloClient:
    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient | None = None,
        *,
        max_attempts: int = 3,
    ):
        if (
            not isinstance(token, str)
            or not token
            or any(c.isspace() for c in token)
            or any(c in token for c in "/?#\\")
        ):
            raise ValueError("Invalid Zalo bot token format.")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5.")
        self._base_url = "https://bot-api.zaloplatforms.com/bot" + token
        self._owns_client = client is None
        self._client = client if client is not None else httpx.AsyncClient(follow_redirects=False)
        self._max_attempts = max_attempts

    async def close(self) -> None:
        """Close an internally created client; injected clients remain caller-owned."""
        if self._owns_client:
            await self._client.aclose()

    async def get_me(self) -> dict:
        result = await self._request("getMe", {})
        if not isinstance(result.get("id"), (str, int)) or isinstance(result.get("id"), bool) or not str(result["id"]).strip():
            raise ZaloAPIError("Zalo returned invalid bot information.", method="getMe")
        return result

    async def get_webhook_info(self) -> dict:
        try:
            result = await self._request("getWebhookInfo", {})
        except ZaloAPIError as error:
            # Observed on a valid bot: HTTP 200, ok:false, error_code:404.
            # Do not invent an empty URL or suppress errors from other methods.
            if error.code == 404:
                raise WebhookInspectionUnavailable(
                    "Zalo webhook inspection is unavailable; state is unverified.",
                    code=error.code, status_code=error.status_code, method="getWebhookInfo",
                ) from None
            raise
        if not isinstance(result.get("url"), str):
            raise ZaloAPIError("Zalo webhook status could not be verified.", method="getWebhookInfo")
        return result

    async def poll(self, timeout: int = 30) -> dict | None:
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 60:
            raise ValueError("Polling timeout must be between 1 and 60 seconds.")
        return await self._request("getUpdates", {"timeout": timeout}, poll=True, read_timeout=timeout + 10)

    async def send_text_chunk(self, chat_id: str, text: str) -> dict:
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("A nonempty chat ID string is required.")
        if not isinstance(text, str) or not text:
            raise ValueError("Message text must not be empty.")
        # UTF-16 units are conservative for the documented 2000-character cap.
        if any(0xD800 <= ord(char) <= 0xDFFF for char in text) or sum(2 if ord(char) > 0xFFFF else 1 for char in text) > 2000:
            raise ValueError("Message text exceeds the supported character limit or contains invalid Unicode.")
        result = await self._request("sendMessage", {"chat_id": chat_id, "text": text}, sending=True)
        if not isinstance(result.get("message_id"), (str, int)) or isinstance(result.get("message_id"), bool) or not str(result["message_id"]).strip():
            raise DeliveryUncertain("Zalo did not confirm a message ID; no automatic resend.", method="sendMessage")
        return result

    async def _request(
        self,
        method: str,
        body: dict,
        *,
        sending: bool = False,
        poll: bool = False,
        read_timeout: int = 20,
    ) -> dict | None:
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.post(
                    self._base_url + "/" + method,
                    json=body,
                    timeout=httpx.Timeout(10, read=read_timeout),
                    follow_redirects=False,
                )
            except httpx.RequestError:
                if sending:
                    raise DeliveryUncertain("Zalo send outcome is unknown; no automatic resend.", method=method) from None
                if attempt + 1 == self._max_attempts:
                    raise ZaloAPIError("Zalo connection failed after bounded retries.", method=method) from None
                await self._backoff(attempt, None)
                continue

            status = response.status_code
            retry_after = _retry_after(response.headers.get("Retry-After"))
            try:
                payload = response.json()
            except ValueError:
                payload = None

            # HTTP failures outrank the JSON envelope. Never accept a 5xx as a
            # known rejection merely because its body happens to say 429.
            if not 200 <= status < 300:
                code = status
            elif isinstance(payload, dict) and payload.get("ok") is False:
                code = _error_code(payload.get("error_code"))
            elif isinstance(payload, dict) and payload.get("ok") is True:
                result = payload.get("result")
                if isinstance(result, dict):
                    return result
                if poll and result is None:
                    return None
                error = DeliveryUncertain if sending else ZaloAPIError
                raise error("Zalo returned an invalid result; request was not repeated.", status_code=status, method=method) from None
            else:
                error = DeliveryUncertain if sending else ZaloAPIError
                raise error("Zalo returned an invalid response; request was not repeated.", status_code=status, method=method) from None

            attributes = {"code": code, "retry_after": retry_after, "status_code": status, "method": method}
            if code == 401:
                raise AuthenticationError("Zalo bot token was rejected.", **attributes) from None
            if poll and code == 408:
                return None
            if sending and (code in (403, 408) or code is not None and code >= 500):
                raise DeliveryUncertain("Zalo send outcome is unknown; no automatic resend.", **attributes) from None

            # 403 is an internal error in the Zalo Bot error-code table.
            retryable = code == 429 or not sending and (code in (403, 408) or code is not None and code >= 500)
            if retryable and attempt + 1 < self._max_attempts:
                await self._backoff(attempt, retry_after)
                continue
            raise ZaloAPIError("Zalo API rejected the request.", **attributes) from None

        raise ZaloAPIError("Zalo retry limit reached.", method=method)  # Defensive; loop always returns or raises.

    @staticmethod
    async def _backoff(attempt: int, retry_after: float | None) -> None:
        delay = min(30.0, 2 ** attempt + random.uniform(0, 0.5))
        await asyncio.sleep(max(delay, retry_after or 0.0))
