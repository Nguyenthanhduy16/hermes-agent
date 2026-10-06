import io
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hermes_zalo.config import ConfigError, Settings, load_env
from hermes_zalo.events import is_allowed, parse_update, split_text
from hermes_zalo.logging_utils import SecretFilter
from hermes_zalo.state import StateStore


def event(chat="chat-1", user="user-1", kind="PRIVATE", mid="m1", text="Xin chào"):
    return {"event_name": "message.text.received", "message": {
        "from": {"id": user, "is_bot": False},
        "chat": {"id": chat, "chat_type": kind},
        "message_id": mid, "text": text,
    }}


class CoreTests(unittest.TestCase):
    def test_group_needs_both_user_and_group(self):
        message = parse_update(event(kind="GROUP"))
        for users, groups in [((), ()), (("user-1",), ()), ((), ("chat-1",))]:
            self.assertFalse(is_allowed(message, frozenset(users), frozenset(groups)))
        self.assertTrue(is_allowed(message, frozenset(["user-1"]), frozenset(["chat-1"])))

    def test_dm_does_not_accept_unapproved_sender(self):
        self.assertFalse(is_allowed(parse_update(event()), frozenset(), frozenset()))

    def test_sessions_and_dedupe_scope(self):
        a = parse_update(event(kind="GROUP"))
        b = parse_update(event(kind="GROUP", user="user-2", mid="m2"))
        c = parse_update(event(chat="chat-2", kind="GROUP"))
        d = parse_update(event())
        self.assertEqual(4, len({m.session_key for m in (a, b, c, d)}))
        self.assertNotEqual(a.key, c.key)

    def test_unsupported_events_and_bot_messages_are_ignored(self):
        for payload in ({}, [], {"update_id": 1}, {"message": "bad"}):
            self.assertIsNone(parse_update(payload))
        payload = event()
        payload["message"]["from"]["is_bot"] = True
        self.assertIsNone(parse_update(payload))
        payload = event()
        payload["event_name"] = "message.image.received"
        self.assertIsNone(parse_update(payload))
        payload = event(kind="unknown")
        self.assertIsNone(parse_update(payload))

    def test_long_unicode_message_roundtrips(self):
        text = "Xin chào 👩🏽‍💻\n" * 1200
        chunks = split_text(text)
        self.assertEqual(text, "".join(chunks))
        self.assertTrue(all(0 < len(c.encode("utf-16-le")) // 2 <= 2000 for c in chunks))

    def test_reservation_survives_restart_and_uncertain_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            first = StateStore(path)
            self.assertTrue(first.reserve("m1"))
            first.finish("m1", "uncertain")
            first.close()
            second = StateStore(path)
            self.assertFalse(second.reserve("m1"))
            self.assertTrue(second.reserve("m2"))
            second.close()

    def test_config_fails_closed_and_never_repr_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                Settings.from_env()
            os.environ["ZALO_BOT_TOKEN"] = "123:secret-token"
            with self.assertRaises(ConfigError):
                Settings.from_env()
            os.environ["ZALO_ALLOWED_USERS"] = "*"
            with self.assertRaises(ConfigError):
                Settings.from_env()
            os.environ["ZALO_ALLOWED_USERS"] = "alice"
            self.assertNotIn("secret-token", repr(Settings.from_env()))

    def test_env_is_literal_and_existing_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"EXISTING": "yes"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('EXISTING=no\nLITERAL="$(not-executed)"\n', encoding="utf-8")
            load_env(path)
            self.assertEqual("yes", os.environ["EXISTING"])
            self.assertEqual("$(not-executed)", os.environ["LITERAL"])

    def test_token_redacted_from_log(self):
        with patch.dict(os.environ, {"ZALO_BOT_TOKEN": "123:secret-token"}):
            record = logging.LogRecord("test", logging.INFO, "", 0, "URL %s", ("https://host/bot123:secret-token/getMe",), None)
            SecretFilter().filter(record)
            self.assertNotIn("secret-token", record.getMessage())

    def test_token_redacted_from_formatted_traceback(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.addFilter(SecretFilter())
        logger = logging.Logger("privacy-test")
        logger.addHandler(handler)
        with patch.dict(os.environ, {"ZALO_BOT_TOKEN": "123:secret-token"}):
            try:
                raise RuntimeError("https://host/bot123:secret-token/sendMessage")
            except RuntimeError:
                logger.exception("Failure")
        self.assertNotIn("secret-token", output.getvalue())
        self.assertIn("REDACTED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
