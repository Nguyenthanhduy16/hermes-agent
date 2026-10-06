"""Google authentication boundaries without real keys or network access."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hermes_zalo.sheets import GoogleTokenProvider, SheetsSettings, SheetsError


class GoogleAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import google.oauth2.service_account
            import google.auth.transport.requests
        except ImportError:
            self.skipTest("Optional Google libraries require SetupSheets")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "account.json"
        self.settings = SheetsSettings("fixture-sheet-123", self.path)

    async def test_untrusted_token_endpoint_rejected_before_credentials_or_http(self):
        self.path.write_text(json.dumps({"type": "service_account", "token_uri": "https://example.invalid/steal"}), encoding="utf-8")
        with patch("google.oauth2.service_account.Credentials.from_service_account_info") as credentials:
            with self.assertRaises(SheetsError) as caught:
                await GoogleTokenProvider(self.settings)()
        credentials.assert_not_called()
        self.assertNotIn("steal", str(caught.exception))

    async def test_refresh_is_bounded_and_token_cached_without_logging(self):
        self.path.write_text(json.dumps({"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"}), encoding="utf-8")
        request = Mock(return_value=SimpleNamespace(status=200))
        credentials = SimpleNamespace(valid=False, token="fake-local-token")
        def refresh(callback):
            callback("https://oauth2.googleapis.com/token", method="POST", body=b"fake")
            credentials.valid = True
        credentials.refresh = Mock(side_effect=refresh)
        with patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=credentials), \
             patch("google.auth.transport.requests.Request", return_value=request):
            provider = GoogleTokenProvider(self.settings)
            self.assertEqual(await provider(), "fake-local-token")
            self.assertEqual(await provider(), "fake-local-token")
        credentials.refresh.assert_called_once()
        self.assertEqual(request.call_args.kwargs["timeout"], 20)
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    async def test_upstream_exception_does_not_expose_key_or_token(self):
        self.path.write_text(json.dumps({"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"}), encoding="utf-8")
        with patch("google.oauth2.service_account.Credentials.from_service_account_info", side_effect=ValueError("PRIVATE-KEY-DO-NOT-LOG")):
            with self.assertRaises(SheetsError) as caught:
                await GoogleTokenProvider(self.settings)()
        self.assertNotIn("PRIVATE-KEY", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
