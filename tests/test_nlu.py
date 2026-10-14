"""Offline model-contract and command-routing checks; no real roster or writes."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from email.utils import format_datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from hermes_zalo import nlu
from hermes_zalo.commands import parse_calendar
from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.sheets import SheetsSettings
from hermes_zalo.work import WorkService


def intent(name, **fields):
    return dict(intent=name, personal=False, when='', period='', person='', room='', course='',
                title='', description='', needs='', **{}) | fields


def completion(data, **fields):
    return {'choices': [dict(finish_reason='stop', message={'content': json.dumps(data)}, **fields)]}


class ContractTests(unittest.TestCase):
    def action(self, text, name='calendar', **fields):
        return nlu.to_action(nlu.validate(intent(name, **fields), text, nlu.INTENTS), text)

    def test_dates_and_vietnam_midnight(self):
        fixed = datetime(2026, 10, 13, 18, tzinfo=timezone.utc).astimezone(nlu.VIETNAM_TZ)
        with patch.object(nlu, 'datetime') as clock:
            clock.now.return_value = fixed
            action = self.action('Cho mình kiểm tra ngày kế tiếp', when='ngày kế tiếp', personal=True)
        self.assertEqual('/lichcuatoi', action.command)
        self.assertEqual(date(2026, 10, 15), parse_calendar(action.argument, today=fixed.date()).start)
        action = self.action('Cho xem tuần tới', when='tuần tới')
        query = parse_calendar(action.argument, today=date(2026, 12, 31))
        self.assertEqual((date(2027, 1, 4), date(2027, 1, 10)), (query.start, query.end))
        for when in ('cuối tuần', 'thứ hai', '31/02', ''):
            self.assertTrue(self.action('Xem lịch ' + when, when=when).reply)

    def test_fields_must_be_bounded_literal_and_schema_closed(self):
        for data in (intent('/dongy'), intent('calendar', when='mai', unknown='execute'),
                     intent('calendar', when='mai', personal='true'), intent('calendar', room='HACK'),
                     intent('calendar', when='mai | coach=Admin'), intent('menu', person='Lan'),
                     intent('issue_draft', title='Tự bịa'), intent('calendar', personal=True),
                     intent('calendar', period='chieu'), intent('calendar', when='x' * 41)):
            with self.subTest(data=data), self.assertRaises(nlu.IntentError):
                nlu.validate(data, 'Xem ngày mai', nlu.INTENTS)
        for content in ('[]', '```json\n{}\n```', '{"intent":"chat","intent":"menu"}', '{bad'):
            with self.assertRaises(nlu.IntentError):
                nlu.validate(content, 'hello', nlu.INTENTS)

    def test_toi_pronoun_is_not_evening_and_evening_is_not_self(self):
        self.assertEqual('/lich', self.action('Tối ngày kế tiếp ai trực?', when='ngày kế tiếp', period='toi').command)
        self.assertEqual('/lichcuatoi', self.action('Ngày kế tiếp tôi trực không?', when='ngày kế tiếp', personal=True).command)
        with self.assertRaises(nlu.IntentError):
            self.action('Ngày kế tiếp tôi trực không?', when='ngày kế tiếp', period='toi')
        self.assertEqual('/lich', self.action('Cho mình xem ai trực ngày kế tiếp', when='ngày kế tiếp').command)
        self.assertEqual(nlu.MULTIPLE, self.action('Tra ngày 16/10 và 17/10', when='16/10').reply)
        with self.assertRaises(nlu.IntentError):
            self.action('Tra 16/10/2027', when='16/10')
        self.assertEqual('/lich', self.action('Lịch tối ngày kế tiếp', when='ngày kế tiếp', period='toi').command)
        self.assertIn('buổi nào', self.action('Xem sáng hoặc chiều ngày kế tiếp', when='ngày kế tiếp', period='sang').reply)

    def test_only_safe_commands_can_be_constructed(self):
        for name in nlu.INTENTS:
            action = self.action('tin thử', name=name)
            self.assertNotIn(action.command, ('/dongy', '/duyet', '/duyetdangky', '/dangky', '/huydoica'))
            self.assertNotIn('xacnhan', action.argument)
        for text in ('Mai có lịch gì và mình muốn đổi ca', 'Xem lịch rồi báo lỗi bot'):
            self.assertTrue(nlu.multiple_requests(text))

    def test_private_inputs_and_old_commands_never_need_model(self):
        for text in ('/menu', '/doica gui 1234', '/dongy 1234', '/dangky 0900000001'):
            self.assertFalse(nlu.should_classify(text, '/menu', ''))
        for text in ('đăng ký giúp mình', 'sdt 0900000001', '\u200b+84 900 000 001'):
            self.assertTrue(nlu.private_input(text))
        self.assertFalse(nlu.private_input('Lịch 16/10/2026'))

    def test_invalid_options(self):
        for values in ({'ZALO_NLU_ENABLED': 'yes'}, {'ZALO_NLU_TIMEOUT_SECONDS': '0'},
                       {'ZALO_NLU_TIMEOUT_SECONDS': '3.5'}, {'ZALO_NLU_TIMEOUT_SECONDS': '99'},
                       {'ZALO_NLU_RESPONSE_FORMAT': 'text'}, {'ZALO_NLU_MAX_TOKENS': '255'},
                       {'ZALO_NLU_MAX_TOKENS': '8193'}, {'ZALO_NLU_MAX_TOKENS': '2.5'},
                       {'ZALO_NLU_MAX_RETRIES': '-1'}, {'ZALO_NLU_MAX_RETRIES': '4'},
                       {'ZALO_NLU_THINKING': 'yes'}):
            with self.assertRaises(ValueError):
                nlu.options(values)

    def test_transport_options_keep_original_enabled_timeout_api(self):
        self.assertEqual((False, 12), nlu.options({}))
        self.assertEqual({'response_format': 'json_schema', 'max_tokens': 2048,
                          'max_retries': 2, 'thinking': 'default'}, nlu.transport_options({}))
        values = {'ZALO_NLU_ENABLED': 'true', 'ZALO_NLU_TIMEOUT_SECONDS': '30',
                  'ZALO_NLU_RESPONSE_FORMAT': 'json_object', 'ZALO_NLU_MAX_TOKENS': '4096',
                  'ZALO_NLU_MAX_RETRIES': '0', 'ZALO_NLU_THINKING': 'disabled'}
        self.assertEqual((True, 30), nlu.options(values))
        self.assertEqual({'response_format': 'json_object', 'max_tokens': 4096,
                          'max_retries': 0, 'thinking': 'disabled'}, nlu.transport_options(values))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.responses = []
        self.response = httpx.Response(200, json=completion(intent('calendar', when='ngày kế tiếp')))
        self.endpoint = nlu.Endpoint('openrouter/free', 'openrouter', 'https://model.invalid/api/v1', 'private-test-key')
        self.resolver = Mock(return_value=self.endpoint)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.router = nlu.IntentRouter(client=self.client, resolver=self.resolver)

    def respond(self, request):
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else self.response

    async def asyncTearDown(self):
        await self.client.aclose()

    async def classify(self, text='Cho xem ngày kế tiếp', allowed=nlu.INTENTS):
        return await self.router.classify(text, allowed=allowed, user_key='private-user-hash')

    async def test_reuses_model_strict_schema_no_tools_history_or_ids(self):
        self.assertEqual('calendar', (await self.classify())['intent'])
        request = self.requests[0]
        body = json.loads(request.content)
        self.assertEqual('openrouter/free', body['model'])
        self.assertEqual({'require_parameters': True}, body['provider'])
        self.assertEqual('json_schema', body['response_format']['type'])
        self.assertTrue(body['response_format']['json_schema']['strict'])
        self.assertEqual(2048, body['max_tokens'])
        self.assertNotIn('thinking', body)
        self.assertNotIn('tools', body)
        self.assertNotIn('private-user-hash', request.content.decode())
        self.assertNotIn('private-test-key', request.content.decode())
        self.assertEqual(2, len(body['messages']))
        self.assertNotIn('private-test-key', repr(self.endpoint))

    async def test_failures_cool_down_without_retry_or_plaintext_fallback(self):
        for status in (400, 401, 402, 403, 404, 422, 500):
            self.router._cooldown = 0
            self.response = httpx.Response(status, text='SECRET PROVIDER BODY')
            before = len(self.requests)
            with self.assertRaises(nlu.IntentError) as error:
                await self.classify()
            self.assertNotIn('SECRET', str(error.exception))
            with self.assertRaises(nlu.IntentError):
                await self.classify()
            self.assertEqual(before + 1, len(self.requests))

    async def test_json_object_has_schema_and_same_local_validation(self):
        self.resolver.return_value = replace(self.endpoint, provider='custom')
        self.router = nlu.IntentRouter(client=self.client, resolver=self.resolver,
                                      **nlu.transport_options({'ZALO_NLU_RESPONSE_FORMAT': 'json_object',
                                                               'ZALO_NLU_MAX_TOKENS': '4096',
                                                               'ZALO_NLU_THINKING': 'disabled'}))
        self.assertEqual('calendar', (await self.classify())['intent'])
        body = json.loads(self.requests[-1].content)
        self.assertEqual({'type': 'json_object'}, body['response_format'])
        self.assertEqual(4096, body['max_tokens'])
        self.assertEqual({'type': 'disabled'}, body['thinking'])
        self.assertNotIn('provider', body)
        self.assertEqual(nlu.SCHEMA, json.loads(body['messages'][0]['content'].split('\n')[-1]))
        for data in (intent('calendar', when='ngày kế tiếp', execute='bad'),
                     intent('calendar', when='ngày kế tiếp', person='Invented Person'),
                     intent('calendar', when='ngày kế tiếp', personal='true')):
            self.response = httpx.Response(200, json=completion(data))
            before = len(self.requests)
            with self.assertRaises(nlu.IntentError):
                await self.classify()
            self.assertEqual(before + 1, len(self.requests))

    def retry_clock(self):
        self.now, self.waits = 100.0, []
        async def sleep(delay):
            self.waits.append(delay)
            self.now += delay
        self.router.clock = lambda: self.now
        self.router.wall_clock = lambda: 1700000000 + self.now - 100
        self.router.sleep = sleep

    async def test_429_retry_after_then_success(self):
        self.retry_clock()
        self.responses = [httpx.Response(429, headers={'Retry-After': '2'}, text='PRIVATE ERROR'), self.response]
        self.assertEqual('calendar', (await self.classify())['intent'])
        self.assertEqual([2], self.waits)
        self.assertEqual(2, len(self.requests))
        self.assertEqual(self.requests[0].content, self.requests[1].content)
        self.resolver.assert_called_once()
        await self.classify()
        self.assertEqual(3, len(self.requests))

    async def test_429_exhaustion_is_bounded_and_cools_down(self):
        self.retry_clock()
        self.response = httpx.Response(429, text='PRIVATE ERROR')
        with self.assertRaisesRegex(nlu.IntentError, '^rate_limited$'):
            await self.classify()
        self.assertEqual([1, 2], self.waits)
        self.assertEqual(3, len(self.requests))
        self.assertEqual(self.now + 60, self.router._cooldown)
        with self.assertRaisesRegex(nlu.IntentError, '^rate_limited$'):
            await self.classify()
        self.assertEqual(3, len(self.requests))

    async def test_retry_after_http_date(self):
        self.retry_clock()
        when = datetime.fromtimestamp(self.router.wall_clock() + 3, timezone.utc)
        self.responses = [httpx.Response(429, headers={'Retry-After': format_datetime(when, usegmt=True)}), self.response]
        self.assertEqual('calendar', (await self.classify())['intent'])
        self.assertEqual([3], self.waits)

    async def test_daily_retry_after_cools_down_without_waiting(self):
        self.retry_clock()
        self.responses = [httpx.Response(429, headers={'Retry-After': '86400'})]
        with self.assertRaisesRegex(nlu.IntentError, '^rate_limited$'):
            await self.classify()
        self.assertEqual([], self.waits)
        self.assertEqual(1, len(self.requests))
        self.now += 61
        with self.assertRaisesRegex(nlu.IntentError, '^rate_limited$'):
            await self.classify()
        self.assertEqual(1, len(self.requests))
        self.now += 86400
        self.assertEqual('calendar', (await self.classify())['intent'])

    async def test_zero_retries_and_malformed_retry_after(self):
        self.retry_clock()
        self.router.max_retries = 0
        self.response = httpx.Response(429, headers={'Retry-After': 'bad'})
        with self.assertRaisesRegex(nlu.IntentError, '^rate_limited$'):
            await self.classify()
        self.assertEqual([], self.waits)
        self.assertEqual(1, len(self.requests))
        for header in ('bad', '-3', 'nan', 'inf', 'x' * 129):
            self.assertEqual(2, self.router._retry_delay(header, 1))
        self.assertEqual(1, self.router._retry_delay('0', 0))

    async def test_retries_share_one_deadline_including_network_time(self):
        self.retry_clock()
        self.router.timeout = 5
        def rate_limited(request):
            self.requests.append(request)
            self.now += 2
            return httpx.Response(429)
        async with httpx.AsyncClient(transport=httpx.MockTransport(rate_limited)) as client:
            self.router.client = client
            with self.assertRaisesRegex(nlu.IntentError, '^rate_limited$'):
                await self.classify()
        self.assertEqual([1], self.waits)
        self.assertEqual(2, len(self.requests))
        self.assertEqual([5, 2], [request.extensions['timeout']['read'] for request in self.requests])

    async def test_cancelled_retry_wait_never_sends_next_request(self):
        self.retry_clock()
        waiting = asyncio.Event()
        async def wait(delay):
            waiting.set()
            await asyncio.sleep(30)
        self.router.sleep = wait
        self.response = httpx.Response(429)
        task = asyncio.create_task(self.classify())
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(1, len(self.requests))

    async def test_invalid_truncated_tool_and_unavailable_intents_fail_closed(self):
        valid = json.dumps(intent('calendar', when='ngày kế tiếp'))
        samples = [httpx.Response(200, json={}), httpx.Response(200, json=completion({'intent': 'menu'})),
                   httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': valid}}]}),
                   httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': valid, 'tool_calls': [1]}}]}),
                   httpx.Response(200, text='x' * 70000)]
        for response in samples:
            self.response = response
            before = len(self.requests)
            with self.assertRaises(nlu.IntentError):
                await self.classify()
            self.assertEqual(before + 1, len(self.requests))
        self.response = httpx.Response(200, json=completion(intent('calendar', when='ngày kế tiếp')))
        with self.assertRaises(nlu.IntentError):
            await self.classify(allowed={'chat'})

    async def test_phone_length_limits_and_per_user_limit_before_network(self):
        for text in ('0900000001', 'x' * 2001):
            with self.assertRaises(nlu.IntentError):
                await self.classify(text)
        self.assertEqual([], self.requests)
        for _ in range(10):
            await self.classify()
        with self.assertRaises(nlu.IntentError):
            await self.classify()
        self.assertEqual(10, len(self.requests))

    async def test_timeout_and_cancellation_do_not_leave_retry(self):
        async def wait(request):
            await asyncio.sleep(1)
        client = httpx.AsyncClient(transport=httpx.MockTransport(wait))
        try:
            self.router.client = client
            self.router.timeout = 0.01
            with self.assertRaisesRegex(nlu.IntentError, 'timeout'):
                await self.classify()
            self.router.timeout = 10
            task = asyncio.create_task(self.classify())
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            await client.aclose()

    async def test_http_client_timeout_has_fixed_timeout_category(self):
        def wait(request):
            raise httpx.ReadTimeout('PRIVATE ERROR', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(wait)) as client:
            self.router.client = client
            with self.assertRaisesRegex(nlu.IntentError, '^timeout$'):
                await self.classify()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.router = SimpleNamespace(classify=AsyncMock(), close=AsyncMock())
        settings = Settings('synthetic', frozenset({'u1', 'u2'}), frozenset({'g1'}),
                            state_dir=Path(self.temp.name), knowledge_dir=Path(self.temp.name) / 'empty', group_access='members')
        sheets_settings = SheetsSettings('synthetic-sheet', Path('not-read'), users=frozenset({'u1', 'u2'}),
                                        groups=frozenset({'g1'}), calendar_format='lab', issues_enabled=True)
        self.sheets = SimpleNamespace(settings=sheets_settings, lab_calendar=AsyncMock(return_value='Lịch từ nguồn mô phỏng'),
                                      close=AsyncMock(), append_issue=AsyncMock())
        self.work = WorkService(settings, sheets_client=self.sheets, environ={}, intent_router=self.router)
        self.sequence = 0

    async def asyncTearDown(self):
        await self.work.close()
        self.temp.cleanup()

    def message(self, text, user='u1', chat='dm1', kind='PRIVATE'):
        self.sequence += 1
        return IncomingMessage(chat, user, str(self.sequence), kind, text)

    async def say(self, text, **kwargs):
        return await self.work.handle(self.message(text, **kwargs))

    async def test_known_rules_commands_and_phones_bypass_ai(self):
        for text in ('/menu', '/lich mai', '/dangky trangthai', 'Mai mình có lịch không?',
                     'Chiều mai ai trực lab?', 'Xem lịch tuần sau của mình.', '0900000001'):
            await self.say(text)
        with patch.object(self.work.knowledge, 'answer', return_value='Nguồn được cấp quyền') as knowledge:
            self.assertEqual('Nguồn được cấp quyền', await self.say('Quy định đăng ký nghỉ phép?'))
            knowledge.assert_called_once()
        self.router.classify.assert_not_awaited()

    async def test_new_calendar_uses_registered_profile_and_original_destination(self):
        self.work.profiles.set(self.message(''), 'Synthetic Coach')
        self.router.classify.return_value = intent('calendar', when='ngày kế tiếp', personal=True)
        response = await self.say('Cho mình coi ngày kế tiếp nhé')
        self.assertIn('mô phỏng', response)
        args = self.sheets.lab_calendar.await_args.args
        self.assertIn(('coach', 'Synthetic Coach'), args[0].filters)
        self.assertEqual(('u1', 'dm1', 'PRIVATE'), args[1:])
        self.assertNotIn('Synthetic Coach', self.router.classify.await_args.args[0])

    async def test_unauthorized_private_and_group_cannot_call_ai(self):
        for kwargs in ({'user': 'stranger'}, {'chat': 'other-group', 'kind': 'GROUP'}):
            self.assertIn('chưa được cấp quyền', await self.say('Coi ngày kế tiếp', **kwargs))
        self.router.classify.assert_not_awaited()

    async def test_group_visitor_cannot_use_ai_to_create_issue(self):
        self.router.classify.return_value = intent('issue_draft', title='lỗi', description='bot hỏng')
        response = await self.say('Ghi lại lỗi này giúp, bot hỏng', user='visitor', chat='g1', kind='GROUP')
        self.assertEqual(nlu.UNAVAILABLE, response)
        self.assertNotIn('issue_draft', self.router.classify.await_args.kwargs['allowed'])
        self.assertEqual(0, self.work.issues.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])

    async def test_issue_draft_needs_explicit_same_person_same_chat_confirmation(self):
        self.router.classify.return_value = intent('issue_draft', title='Không lưu', description='Bấm nút nhưng không có phản hồi')
        text = 'Ghi nhận sự cố Không lưu, mô tả Bấm nút nhưng không có phản hồi'
        msg = self.message(text)
        response = await self.work.handle(msg)
        self.assertIn('/issue xacnhan', response)
        await self.work.handle(msg)  # same Zalo event key reuses the same draft
        self.assertEqual(1, self.work.issues.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])
        draft = self.work.issues.db.execute('SELECT id FROM drafts').fetchone()[0]
        self.sheets.append_issue.assert_not_awaited()
        for kwargs in ({'user': 'u2'}, {'chat': 'dm2'}):
            await self.say('/issue xacnhan ' + draft, **kwargs)
        self.sheets.append_issue.assert_not_awaited()
        await self.say('/issue xacnhan ' + draft)
        self.sheets.append_issue.assert_awaited_once()

    async def test_disabled_feature_model_error_multiple_and_unknown_internal_work(self):
        self.sheets.settings = replace(self.sheets.settings, issues_enabled=False)
        self.router.classify.return_value = intent('issue_help')
        self.assertEqual(nlu.UNAVAILABLE, await self.say('Ghi nhận sự cố giúp mình'))
        self.router.classify.side_effect = nlu.IntentError('timeout')
        self.assertEqual(nlu.UNAVAILABLE, await self.say('Coi ngày kế tiếp'))
        self.assertIn('Tra lịch', await self.say('/menu'))
        count = self.router.classify.await_count
        self.assertEqual(nlu.MULTIPLE, await self.say('Xem lịch và mình muốn đổi ca'))
        self.assertEqual(count, self.router.classify.await_count)
        self.router.classify.side_effect = None
        self.router.classify.return_value = intent('chat')
        self.assertIn('kho tài liệu', await self.say('Ai phụ trách phòng lab?'))

    async def test_no_context_shared_between_users_or_chats(self):
        self.router.classify.return_value = intent('clarify', needs='unclear')
        for kwargs in ({}, {'user': 'u2'}, {'chat': 'g1', 'kind': 'GROUP'}):
            response = await self.say('Thế còn hôm sau?', **kwargs)
            self.assertIn('câu đầy đủ', response)
        for call in self.router.classify.await_args_list:
            self.assertEqual(('Thế còn hôm sau?',), call.args)
            self.assertEqual({'allowed', 'user_key'}, set(call.kwargs))


class RegisteredTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_registration_work as fixture
        self.fixture = fixture.RegistrationWorkTests('runTest')
        await self.fixture.asyncSetUp()
        self.router = SimpleNamespace(classify=AsyncMock(), close=AsyncMock())
        self.fixture.service.intent_router = self.router

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def test_unapproved_and_duplicate_name_registration_boundaries(self):
        f = self.fixture
        await f.service.handle(f.message('Cho mình coi ngày kế tiếp', user='new-user'))
        self.router.classify.assert_not_awaited()
        record = await f.approve(user='new-user')
        self.router.classify.return_value = intent('calendar', when='06/10/2026', personal=True)
        text = 'Kiểm tra phần của mình vào 06/10/2026 nhé'
        response = await f.service.handle(f.message(text, user='new-user'))
        self.assertIn('MY-COURSE', response)
        self.assertNotIn('PREFIX-OTHER', response)
        f.directory.person = replace(f.directory.person, name_unique=False)
        response = await f.service.handle(f.message(text, user='new-user'))
        self.assertNotIn('MY-COURSE', response)

    async def test_revocation_while_model_running_blocks_action(self):
        f = self.fixture
        record = await f.approve(user='new-user')
        async def revoke(*args, **kwargs):
            f.service.registration.store.revoke('admin', record.member_id)
            return intent('calendar', when='06/10/2026', personal=True)
        self.router.classify.side_effect = revoke
        response = await f.service.handle(f.message('Kiểm tra phần của mình vào 06/10/2026', user='new-user'))
        self.assertIn('Quyền đăng ký đã thay đổi', response)
        f.sheets._lab_schedules.assert_not_awaited()

    async def test_named_calendar_requires_unique_workload_mapping_and_exact_match(self):
        from hermes_zalo.workload import WorkloadError
        f = self.fixture
        await f.approve(user='new-user')
        f.directory.resolve_name = AsyncMock(return_value=f.directory.person)
        self.router.classify.return_value = intent('calendar', when='06/10/2026', person='Coach Alpha')
        text = 'Tra ca của Coach Alpha vào 06/10/2026'
        result = await f.service.handle(f.message(text, user='new-user'))
        self.assertIn('MY-COURSE', result)
        self.assertNotIn('PREFIX-OTHER', result)
        f.directory.resolve_name.side_effect = WorkloadError('Synthetic duplicate')
        result = await f.service.handle(f.message(text, user='new-user'))
        self.assertIn('mã thành viên', result)
        self.assertNotIn('MY-COURSE', result)


class TicketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_registration_ticket_scope as fixture
        self.fixture = fixture.RegistrationTicketScopeTests('runTest')
        await self.fixture.asyncSetUp()
        self.router = SimpleNamespace(classify=AsyncMock(), close=AsyncMock())
        self.fixture.work.intent_router = self.router

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def test_detailed_swap_only_opens_existing_wizard_and_choices_stay_local(self):
        f = self.fixture
        self.router.classify.return_value = intent('tickets_start', when='16/10', period='sang', person='Lãm')
        response = await f.say('a', 'Mình muốn đổi ca sáng ngày 16/10 với Lãm')
        self.assertIn('Bước 1/4', response)
        self.assertIn('Chưa gửi yêu cầu', response)
        count = self.router.classify.await_count
        await f.choose('a', 'CA-11')
        self.assertEqual(count, self.router.classify.await_count)
        f.provider.swap.assert_not_called()

    async def test_freeform_pending_request_and_group_dm_boundary(self):
        f = self.fixture
        self.router.classify.return_value = intent('tickets_pending')
        self.assertIn('CHƯA CÓ', await f.say('b', 'Có yêu cầu đổi ca nào đang chờ mình đồng ý không?'))
        # Group access requires registration before the ticket DM boundary runs.
        await f.approve('a')
        f.work.settings = replace(f.work.settings, allowed_groups=frozenset({'g1'}))
        message = replace(f.message('a', 'Đổi giúp ca sáng 16/10 với Lãm'), chat_id='g1', chat_type='GROUP')
        self.router.classify.return_value = intent('tickets_start', when='16/10', period='sang', person='Lãm')
        self.assertIn('tin nhắn riêng', await f.work.handle(message))
        f.provider.swap.assert_not_called()


if __name__ == '__main__':
    unittest.main()
