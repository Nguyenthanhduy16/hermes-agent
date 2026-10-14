"""Transport preflight checks, using HTTP mocks only."""
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from hermes_zalo.api import DeliveryBlocked, DeliveryUncertain, ZaloClient


class DeliveryGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_denied_guard_makes_no_http_request(self):
        transport = Mock(return_value=httpx.Response(200, json={'ok': True, 'result': {'message_id': 'one'}}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = ZaloClient('fake-test-token', http)
            with self.assertRaises(DeliveryBlocked):
                await client.send_text_chunk('private-a', 'Nhắc lịch mô phỏng', before_send=lambda: False)
        transport.assert_not_called()

    async def test_async_guard_rechecked_after_429_wait(self):
        transport = Mock(return_value=httpx.Response(429, json={}))
        guard = AsyncMock(side_effect=[True, False])
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = ZaloClient('fake-test-token', http)
            with patch.object(client, '_backoff', new_callable=AsyncMock) as backoff:
                with self.assertRaises(DeliveryBlocked):
                    await client.send_text_chunk('private-a', 'Nhắc lịch mô phỏng', before_send=guard)
        self.assertEqual(1, transport.call_count)
        self.assertEqual(2, guard.await_count)
        backoff.assert_awaited_once()

    async def test_unchanged_authorization_allows_known_rate_limit_retry(self):
        transport = Mock(side_effect=[httpx.Response(429, json={}),
            httpx.Response(200, json={'ok': True, 'result': {'message_id': 'one'}})])
        guard = Mock(return_value=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = ZaloClient('fake-test-token', http)
            with patch.object(client, '_backoff', new_callable=AsyncMock):
                result = await client.send_text_chunk('private-a', 'Nhắc lịch mô phỏng', before_send=guard)
        self.assertEqual({'message_id': 'one'}, result)
        self.assertEqual(2, transport.call_count)
        self.assertEqual(2, guard.call_count)

    async def test_guard_does_not_enable_retry_of_ambiguous_send(self):
        transport = Mock(return_value=httpx.Response(500, json={}))
        guard = Mock(return_value=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            client = ZaloClient('fake-test-token', http)
            with self.assertRaises(DeliveryUncertain):
                await client.send_text_chunk('private-a', 'Nhắc lịch mô phỏng', before_send=guard)
        self.assertEqual(1, transport.call_count)
        self.assertEqual(1, guard.call_count)


if __name__ == '__main__':
    unittest.main()
