"""Extension sections: owner-only routing, filter validation, confirmed and audited actions."""
from pathlib import Path
import unittest

from hermes_zalo import console_sections
from hermes_zalo.admin_auth import AdminError
from hermes_zalo.console_sections import Section, register, validate_action

from tests import test_admin_web as base


class RegistryTests(unittest.TestCase):
    def test_names_are_validated_and_built_ins_cannot_be_shadowed(self):
        for name in ('models', 'sessions', 'Bad', 'x', '../up', ''):
            with self.assertRaises(ValueError):
                register(Section(name, lambda ctx, query: {}))

    def test_action_envelope_requires_exact_confirmed_shape(self):
        good = {'action': 'run', 'expected_revision': 'r1', 'confirm': True, 'values': {}}
        self.assertEqual(('run', 'r1', {}), validate_action(good))
        for bad in (dict(good, confirm=False), dict(good, extra=1), dict(good, values=[]),
                    dict(good, expected_revision=True), dict(good, action=''), None):
            with self.assertRaises(AdminError):
                validate_action(bad)


class ExtensionRouteTests(base.AdminHttpTests):
    def setUp(self):
        super().setUp()
        self.seen = []

        def snapshot(ctx, query):
            self.seen.append(('snapshot', ctx, query))
            return {'section': 'synthetic-ext', 'available': True, 'items': [{'id': 'a'}], 'query': query}

        def action(ctx, data):
            action_id, revision, values = validate_action(data)
            self.seen.append(('action', action_id, values))
            return {'ok': True, 'message': 'Done.'}

        register(Section('synthetic-ext', snapshot, action, frozenset({'days'})))
        self.app.runtime_home = Path(self.temp.name) / 'home'

    def tearDown(self):
        console_sections._registry.pop('synthetic-ext', None)
        super().tearDown()

    def owner(self):
        self.auth.create_account('owner', self.PASSWORD, [], role='faculty')
        client = self.client()
        self.assertEqual(200, self.request('POST', '/api/login', client=client,
                                           data={'username': 'owner', 'password': self.PASSWORD})[0])
        return client

    def test_snapshot_is_owner_only_and_filters_are_allowlisted(self):
        reader = self.client(['conversations', 'memories'], 'reader')
        self.assertEqual(403, self.request('GET', '/api/assistant/synthetic-ext', client=reader)[0])
        owner = self.owner()
        status, body, _ = self.request('GET', '/api/assistant/synthetic-ext?days=7', client=owner)
        self.assertEqual(200, status)
        self.assertEqual({'days': '7'}, body['query'])
        context = self.seen[-1][1]
        self.assertEqual(Path(self.temp.name) / 'home', context.home)
        self.assertEqual('local', context.mode)
        self.assertEqual(400, self.request('GET', '/api/assistant/synthetic-ext?other=1', client=owner)[0])
        self.assertEqual(405, self.request('POST', '/api/assistant/synthetic-ext', client=owner, data={})[0])
        self.assertEqual(404, self.request('GET', '/api/assistant/synthetic-ext/other', client=owner)[0])

    def test_actions_need_csrf_confirmation_and_are_audited_without_values(self):
        owner = self.owner()
        payload = {'action': 'run', 'expected_revision': 'r1', 'confirm': True, 'values': {'secret': 'value-not-audited'}}
        path = '/api/assistant/synthetic-ext/action'
        self.assertEqual(403, self.request('POST', path, client=owner, data=payload, csrf=False)[0])
        self.assertEqual(400, self.request('POST', path, client=owner, data=dict(payload, confirm=False))[0])
        self.assertEqual(400, self.request('POST', path + '?x=1', client=owner, data=payload)[0])
        self.assertEqual(405, self.request('GET', path, client=owner)[0])
        status, body, _ = self.request('POST', path, client=owner, data=payload)
        self.assertEqual((200, True), (status, body['ok']))
        self.assertEqual([('action', 'run', {'secret': 'value-not-audited'})], [s for s in self.seen if s[0] == 'action'])
        with self.auth.transaction() as db:
            rows = [tuple(r) for r in db.execute('SELECT * FROM admin_auth_audit')]
        text = repr(rows)
        self.assertIn('synthetic-ext', text)
        self.assertNotIn('value-not-audited', text)


def load_tests(loader, tests, pattern):
    # Only this module's tests, not the inherited HTTP fixture's own test methods.
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(RegistryTests))
    suite.addTests(ExtensionRouteTests(name) for name in loader.getTestCaseNames(ExtensionRouteTests)
                   if name in ExtensionRouteTests.__dict__)
    return suite


if __name__ == '__main__':
    unittest.main()
