"""NLU through actual adapter dispatch with synthetic Zalo events and model."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import test_adapter as contract
from test_nlu import intent
from hermes_zalo.work import WorkService


class NluAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        self.router = SimpleNamespace(classify=AsyncMock(return_value=intent('menu')), close=AsyncMock())
        self.adapter.work = WorkService(self.adapter.settings, environ={}, intent_router=self.router)

    async def asyncTearDown(self):
        await contract.AdapterTests.asyncTearDown(self)

    async def test_group_reply_destination_dedup_and_no_model_tools(self):
        event = contract.update(chat='g1', kind='GROUP', text='Bạn hỗ trợ được việc j nhỉ')
        await self.adapter._dispatch(event)
        await self.adapter._dispatch(event)
        self.router.classify.assert_awaited_once()
        self.handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_awaited_once()
        args = self.adapter.client.send_text_chunk.await_args.args
        self.assertEqual('g1', args[0])
        self.assertIn('MENU BOT', args[1])

    async def test_denied_sender_and_group_never_reach_classifier(self):
        await self.adapter._dispatch(contract.update('d1', user='stranger', text='Bạn hỗ trợ được việc j'))
        await self.adapter._dispatch(contract.update('d2', chat='other-group', kind='GROUP', text='Bạn hỗ trợ được việc j'))
        self.router.classify.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_chat_continues_to_hermes_and_errors_leave_commands_usable(self):
        from hermes_zalo.nlu import IntentError
        self.router.classify.return_value = intent('chat')
        await self.adapter._dispatch(contract.update('chat', text='Kể một chuyện vui đi'))
        self.handler.assert_awaited_once()
        self.router.classify.side_effect = IntentError('invalid')
        await self.adapter._dispatch(contract.update('failure', text='Bạn giúp được việc j'))
        await self.adapter._dispatch(contract.update('menu', text='/menu'))
        self.assertIn('MENU BOT', self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertEqual(2, self.router.classify.await_count)


if __name__ == '__main__':
    unittest.main()
