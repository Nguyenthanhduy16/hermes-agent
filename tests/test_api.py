import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from hermes_zalo.api import AuthenticationError, DeliveryUncertain, WebhookInspectionUnavailable, ZaloAPIError, ZaloClient


TOKEN = "123:example-token-secret"


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = []
        self.backoff = patch.object(ZaloClient, "_backoff", new_callable=AsyncMock)
        self.sleep = self.backoff.start()

    async def asyncTearDown(self):
        self.backoff.stop()
        for client in self.clients:
            await client.aclose()

    def client(self, handler, **kwargs):
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, follow_redirects=True)
        self.clients.append(client)
        return ZaloClient(TOKEN, client, **kwargs)

    async def test_poll_posts_only_timeout_and_unwraps_one_event(self):
        event = {"event_name": "message.text.received", "message": {"chat": {"id": "group.1", "chat_type": "GROUP"}, "from": {"id": "user.2", "is_bot": False}, "message_id": "msg.1", "text": "Xin chào", "date": 1750316131602}}

        def handler(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.host, "bot-api.zaloplatforms.com")
            self.assertEqual(request.url.path, "/bot" + TOKEN + "/getUpdates")
            self.assertEqual(json.loads(request.content), {"timeout": 30})
            self.assertEqual(request.extensions["timeout"]["read"], 40)
            return httpx.Response(200, json={"ok": True, "result": event})

        self.assertEqual(await self.client(handler).poll(), event)

    async def test_poll_api_and_http_408_are_idle(self):
        for status, body in [(200, {"ok": False, "error_code": 408}), (408, {})]:
            with self.subTest(status=status):
                self.assertIsNone(await self.client(lambda _: httpx.Response(status, json=body)).poll())
        self.sleep.assert_not_awaited()

    async def test_read_retries_429_and_bounds_retry_after(self):
        calls = 0

        def handler(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, headers={"Retry-After": "99999"}, json={"ok": False, "error_code": 429})
            return httpx.Response(200, json={"ok": True, "result": {"id": "123", "can_join_groups": False}})

        result = await self.client(handler).get_me()
        self.assertEqual(result["id"], "123")
        self.assertEqual(calls, 2)
        self.sleep.assert_awaited_once_with(0, 60.0)

    async def test_bad_token_is_fatal_and_never_leaks_description(self):
        def handler(_):
            return httpx.Response(200, json={"ok": False, "error_code": 401, "description": TOKEN + " private content"})

        with self.assertRaises(AuthenticationError) as caught:
            await self.client(handler).get_me()
        self.assertEqual(caught.exception.code, 401)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn("private content", str(caught.exception))
        self.sleep.assert_not_awaited()

    async def test_webhook_info_preserves_active_url(self):
        url = "https://example.org/hooks/zalo"
        result = await self.client(lambda _: httpx.Response(200, json={"ok": True, "result": {"url": url, "updated_at": 1234}})).get_webhook_info()
        self.assertEqual(result, {"url": url, "updated_at": 1234})

    async def test_webhook_404_is_unknown_not_confirmed_empty(self):
        for status in (200, 404):
            api = self.client(lambda _: httpx.Response(status, json={
                "ok": False, "error_code": 404, "description": "Not Found " + TOKEN,
            }))
            with self.subTest(status=status), self.assertRaises(WebhookInspectionUnavailable) as caught:
                await api.get_webhook_info()
            self.assertEqual(caught.exception.code, 404)
            self.assertEqual(caught.exception.status_code, status)
            self.assertEqual(caught.exception.method, "getWebhookInfo")
            self.assertNotIn(TOKEN, str(caught.exception))
        self.sleep.assert_not_awaited()

    async def test_other_method_404_is_still_failure(self):
        for method in ("get_me", "poll"):
            api = self.client(lambda _: httpx.Response(200, json={"ok": False, "error_code": 404}))
            with self.subTest(method=method), self.assertRaises(ZaloAPIError) as caught:
                await getattr(api, method)()
            self.assertNotIsInstance(caught.exception, WebhookInspectionUnavailable)

    async def test_webhook_other_errors_are_not_downgraded(self):
        for code in (401, 403, 429, 500):
            api = self.client(lambda _: httpx.Response(200, json={"ok": False, "error_code": code}), max_attempts=1)
            with self.subTest(code=code), self.assertRaises(ZaloAPIError) as caught:
                await api.get_webhook_info()
            self.assertNotIsInstance(caught.exception, WebhookInspectionUnavailable)
            self.assertEqual(caught.exception.method, "getWebhookInfo")

    async def test_missing_bot_webhook_or_send_receipt_is_not_success(self):
        for method, args, error in [
            ("get_me", (), ZaloAPIError),
            ("get_webhook_info", (), ZaloAPIError),
            ("send_text_chunk", ("group.1", "Hello"), DeliveryUncertain),
        ]:
            api = self.client(lambda _: httpx.Response(200, json={"ok": True, "result": {}}))
            with self.subTest(method=method), self.assertRaises(error):
                await getattr(api, method)(*args)

    async def test_send_validates_before_network_and_preserves_chat_id(self):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": "out.1", "date": 123}})

        api = self.client(handler)
        for text in ["", "x" * 2001, "😀" * 1001, "bad\ud800"]:
            with self.assertRaises(ValueError):
                await api.send_text_chunk("group.1", text)
        self.assertEqual(calls, [])
        self.assertEqual((await api.send_text_chunk("group.1", "😀" * 1000))["message_id"], "out.1")
        self.assertEqual(calls[0]["chat_id"], "group.1")
        self.assertEqual(set(calls[0]), {"chat_id", "text"})

    async def test_send_timeout_and_network_errors_are_never_retried(self):
        for exception_type in (httpx.ReadTimeout, httpx.ConnectError):
            calls = 0

            def handler(request):
                nonlocal calls
                calls += 1
                raise exception_type(str(request.url) + " sensitive response", request=request)

            with self.subTest(error=exception_type), self.assertRaises(DeliveryUncertain) as caught:
                await self.client(handler).send_text_chunk("group.1", "Hello")
            self.assertEqual(calls, 1)
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertNotIn("sensitive", str(caught.exception))
        self.sleep.assert_not_awaited()

    async def test_send_server_failure_and_bad_json_are_not_retried(self):
        for status, content in [(503, b'{"ok":false,"error_code":429}'), (200, b"invalid json"), (200, b'{"ok":true,"result":[]}')]:
            calls = 0

            def handler(_):
                nonlocal calls
                calls += 1
                return httpx.Response(status, content=content)

            with self.subTest(status=status, content=content), self.assertRaises(DeliveryUncertain):
                await self.client(handler).send_text_chunk("group.1", "Hello")
            self.assertEqual(calls, 1)
        self.sleep.assert_not_awaited()

    async def test_send_explicit_429_is_retried_with_bounded_attempts(self):
        calls = 0

        def handler(_):
            nonlocal calls
            calls += 1
            return httpx.Response(429, json={"ok": False, "error_code": 429})

        with self.assertRaises(ZaloAPIError) as caught:
            await self.client(handler).send_text_chunk("group.1", "Hello")
        self.assertNotIsInstance(caught.exception, DeliveryUncertain)
        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(calls, 3)
        self.assertEqual(self.sleep.await_count, 2)

    async def test_redirect_is_never_followed_even_for_injected_client(self):
        calls = []

        def handler(request):
            calls.append(request.url.host)
            return httpx.Response(307, headers={"Location": "https://example.org/token"})

        with self.assertRaises(ZaloAPIError):
            await self.client(handler).get_me()
        self.assertEqual(calls, ["bot-api.zaloplatforms.com"])

    async def test_read_network_retries_are_bounded(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            raise httpx.ConnectError(str(request.url), request=request)

        with self.assertRaises(ZaloAPIError) as caught:
            await self.client(handler).get_me()
        self.assertEqual(calls, 3)
        self.assertNotIn(TOKEN, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
