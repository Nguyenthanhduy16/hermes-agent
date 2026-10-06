"""Diagnostics use synthetic clients only; never load local credentials."""

from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hermes_zalo import api, cli


TOKEN = "123:example-cli-token-secret"


class OnlineCheckTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *, webhook=None, webhook_error=None):
        client = Mock(spec=api.ZaloClient)
        client.get_me = AsyncMock(return_value={"id": "test-bot", "can_join_groups": True})
        client.get_webhook_info = AsyncMock(return_value=webhook, side_effect=webhook_error)
        client.poll = AsyncMock(return_value=None)
        client.send_text_chunk = AsyncMock()
        client.close = AsyncMock()
        return client

    def unavailable(self):
        return api.WebhookInspectionUnavailable(
            "Webhook inspection is unavailable.", code=404, status_code=200,
            method="getWebhookInfo",
        )

    async def test_probe_unavailable_webhook_warns_without_polling_or_sending(self):
        client = self.client(webhook_error=self.unavailable())
        output = io.StringIO()
        with (
            patch.dict("os.environ", {"ZALO_BOT_TOKEN": TOKEN}, clear=True),
            patch.object(api, "ZaloClient", return_value=client),
            redirect_stdout(output), redirect_stderr(output),
        ):
            result = await cli.online_check("probe", 1)

        self.assertEqual(result, 0)
        self.assertIn("404", output.getvalue())
        self.assertIn("chưa xác minh", output.getvalue().lower())
        self.assertNotIn("sẵn sàng", output.getvalue().lower())
        self.assertNotIn(TOKEN, output.getvalue())
        client.poll.assert_not_awaited()
        client.send_text_chunk.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_discover_unavailable_webhook_polls_and_prints_only_identity(self):
        client = self.client(webhook_error=self.unavailable())
        client.poll.return_value = {
            "event_name": "message.text.received",
            "message": {
                "from": {"id": "test-user", "is_bot": False},
                "chat": {"id": "test-group", "chat_type": "GROUP"},
                "message_id": "test-message", "text": "private message content",
            },
        }
        clock = Mock()
        clock.time.side_effect = [100, 100, 100, 102]
        output = io.StringIO()
        with (
            patch.dict("os.environ", {"ZALO_BOT_TOKEN": TOKEN}, clear=True),
            patch.object(api, "ZaloClient", return_value=client),
            patch("hermes_zalo.cli.asyncio.get_running_loop", return_value=clock),
            redirect_stdout(output), redirect_stderr(output),
        ):
            result = await cli.online_check("discover", 1)

        self.assertEqual(result, 0)
        self.assertIn("404", output.getvalue())
        self.assertIn("chưa xác minh", output.getvalue().lower())
        self.assertIn('"user_id": "test-user"', output.getvalue())
        self.assertIn('"chat_id": "test-group"', output.getvalue())
        self.assertNotIn("private message content", output.getvalue())
        self.assertNotIn(TOKEN, output.getvalue())
        client.poll.assert_awaited_once_with(timeout=1)
        client.send_text_chunk.assert_not_awaited()
        client.close.assert_awaited_once()

    async def test_active_webhook_stops_probe_and_discovery_without_polling(self):
        for command in ("probe", "discover"):
            with self.subTest(command=command):
                client = self.client(webhook={"url": "https://example.org/private-hook"})
                output = io.StringIO()
                with (
                    patch.dict("os.environ", {"ZALO_BOT_TOKEN": TOKEN}, clear=True),
                    patch.object(api, "ZaloClient", return_value=client),
                    redirect_stdout(output), redirect_stderr(output),
                ):
                    result = await cli.online_check(command, 1)
                self.assertEqual(result, 2)
                self.assertIn("đang có webhook", output.getvalue())
                self.assertNotIn("private-hook", output.getvalue())
                client.poll.assert_not_awaited()
                client.send_text_chunk.assert_not_awaited()
                client.close.assert_awaited_once()

    async def test_other_webhook_errors_stop_discovery_and_close_client(self):
        for code in (401, 429, 503):
            with self.subTest(code=code):
                error = api.ZaloAPIError("Request rejected.", code=code, status_code=200)
                client = self.client(webhook_error=error)
                output = io.StringIO()
                with (
                    patch.dict("os.environ", {"ZALO_BOT_TOKEN": TOKEN}, clear=True),
                    patch.object(api, "ZaloClient", return_value=client),
                    redirect_stdout(output), redirect_stderr(output),
                    self.assertRaises(api.ZaloAPIError),
                ):
                    await cli.online_check("discover", 1)
                client.poll.assert_not_awaited()
                client.send_text_chunk.assert_not_awaited()
                client.close.assert_awaited_once()


class MainDiagnosticsTests(unittest.TestCase):
    def test_api_error_reports_method_and_numeric_status_without_local_credentials(self):
        error = api.ZaloAPIError(
            "Zalo API rejected the request.", code=429, status_code=200,
            method="getWebhookInfo",
        )
        output = io.StringIO()
        with (
            patch("sys.argv", ["zalo-bot", "probe"]),
            patch.object(cli, "load_env"),
            patch.object(cli, "protect_logs"),
            patch.object(cli, "online_check", new=AsyncMock(side_effect=error)),
            redirect_stdout(output), redirect_stderr(output),
        ):
            result = cli.main()
        self.assertEqual(result, 1)
        self.assertIn("getWebhookInfo", output.getvalue())
        self.assertIn("200", output.getvalue())
        self.assertIn("429", output.getvalue())

    def test_unexpected_exception_never_prints_its_message_or_url(self):
        output = io.StringIO()
        with (
            patch("sys.argv", ["zalo-bot", "probe"]),
            patch.object(cli, "load_env"),
            patch.object(cli, "protect_logs"),
            patch.object(cli, "online_check", new=AsyncMock(side_effect=RuntimeError(
                "https://bot-api.zaloplatforms.com/bot" + TOKEN + "/getMe private content"
            ))),
            redirect_stdout(output), redirect_stderr(output),
        ):
            result = cli.main()
        self.assertEqual(result, 1)
        self.assertIn("RuntimeError", output.getvalue())
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertNotIn("https://", output.getvalue())
        self.assertNotIn("private content", output.getvalue())


if __name__ == "__main__":
    unittest.main()
