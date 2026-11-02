"""Small async client for the official Zalo Bot API.

getUpdates yields one event, without Telegram offsets. Read requests retry;
an ambiguous send never retries because Zalo documents no idempotency key.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import inspect
import random

import httpx


MAX_PHOTO_BYTES = 10 * 1024 * 1024


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


class DeliveryBlocked(ZaloAPIError):
    """An authorized sender's final check refused this transport attempt."""


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

    async def send_text_chunk(
        self, chat_id: str, text: str, *,
        before_send: Callable[[], bool | Awaitable[bool]] | None = None,
    ) -> dict:
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("A nonempty chat ID string is required.")
        if not isinstance(text, str) or not text:
            raise ValueError("Message text must not be empty.")
        # UTF-16 units are conservative for the documented 2000-character cap.
        if any(0xD800 <= ord(char) <= 0xDFFF for char in text) or sum(2 if ord(char) > 0xFFFF else 1 for char in text) > 2000:
            raise ValueError("Message text exceeds the supported character limit or contains invalid Unicode.")
        guarded = {"before_send": before_send} if before_send is not None else {}
        result = await self._request("sendMessage", {"chat_id": chat_id, "text": text}, sending=True, **guarded)
        if not isinstance(result.get("message_id"), (str, int)) or isinstance(result.get("message_id"), bool) or not str(result["message_id"]).strip():
            raise DeliveryUncertain("Zalo did not confirm a message ID; no automatic resend.", method="sendMessage")
        return result

    async def send_photo(self, chat_id: str, photo: str | bytes, caption: str | None = None, *,
                         filename: str = "image.png") -> dict:
        """Send an image by public URL, or upload bytes as multipart/form-data.

The docs only show ``photo`` as a URL; multipart upload is the generic
upload path described for all methods and must be verified live.
"""
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("A nonempty chat ID string is required.")
        body = {"chat_id": chat_id}
        if caption:
            body["caption"] = caption[:2000]
        files = None
        if isinstance(photo, bytes):
            if not photo or len(photo) > MAX_PHOTO_BYTES:
                raise ValueError("Image is empty or too large.")
            files = {"photo": (filename, photo)}
        elif isinstance(photo, str) and photo.startswith("https://"):
            body["photo"] = photo
        else:
            raise ValueError("photo must be an https URL or image bytes.")
        result = await self._request("sendPhoto", body, sending=True, files=files)
        if not isinstance(result.get("message_id"), (str, int)) or isinstance(result.get("message_id"), bool):
            raise DeliveryUncertain("Zalo did not confirm a message ID; no automatic resend.", method="sendPhoto")
        return result

    async def send_chat_action(self, chat_id: str, action: str = "typing") -> None:
        await self._request("sendChatAction", {"chat_id": chat_id, "action": action})

    async def download(self, url: str, *, limit: int = MAX_PHOTO_BYTES) -> bytes:
        """Fetch an inbound Zalo media URL with a hard size cap; never sends the token."""
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ZaloAPIError("Media URL must use https.", method="download")
        async with self._client.stream("GET", url, timeout=httpx.Timeout(10, read=30),
                                       follow_redirects=True) as response:
            if response.status_code != 200:
                raise ZaloAPIError("Media download failed.", status_code=response.status_code, method="download")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ZaloAPIError("Media exceeds the size limit.", method="download")
        return bytes(data)

    async def _request(
        self,
        method: str,
        body: dict,
        *,
        files: dict | None = None,
        sending: bool = False,
        poll: bool = False,
        read_timeout: int = 20,
        before_send: Callable[[], bool | Awaitable[bool]] | None = None,
    ) -> dict | None:
        for attempt in range(self._max_attempts):
            if sending and before_send is not None:
                # A known 429 can wait before retrying. Recheck the current
                # recipient and schedule after that wait, before ANY network
                # attempt, without misclassifying denial as network ambiguity.
                allowed = before_send()
                if inspect.isawaitable(allowed):
                    allowed = await allowed
                if allowed is not True:
                    raise DeliveryBlocked("Zalo delivery authorization changed.", method=method)
            try:
                payload_args = {"data": body, "files": files} if files else {"json": body}
                response = await self._client.post(
                    self._base_url + "/" + method,
                    **payload_args,
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
