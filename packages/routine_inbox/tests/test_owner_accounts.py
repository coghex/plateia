"""Owner accounts (#29, staged slice 1) in the inbox core: the optional `owner_accounts` list supplements `owner`,
so a question answered by either account is resolved once and the other account's later reply is retained replay
evidence. Four offline scenarios (the chat bridge's three are in chat/scripts/tests/test_owner_accounts.py), on
invented data and the fake adapter: owner `pat`, second owner account `quill`, assistant `sam`, manager
`alpha-manager`, project `alpha`. Each runs in its own throwaway state directory."""
import contextlib
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from routine_inbox.adapter import FakeAdapter
from routine_inbox.inbox import Blocked, Inbox, config
from routine_inbox.protocol import tags

CFG = {
    'alias': 'helper', 'assistant': 'sam', 'owner': 'pat', 'claim_seconds': 600,
    'projects': {'alpha': {'channel': '#alpha', 'prefix': '#alp-', 'request_prefix': 'alpha-',
                           'manager': 'alpha-manager', 'recipient': 'alpha-lead'}},
}
DECISION = {'kind': 'settled', 'answer': 'Use the approved default', 'source': 'approved issue 17 criterion 2'}


class Bench:
    """One inbox on its own state directory with the fake adapter and a fixed clock."""

    def __init__(self, root, name, **extra):
        self.state, self.adapter, self.now = Path(root) / name, FakeAdapter(), 1800000000.0
        self.open(**extra)
        self.box.initialize()
        self.now += 1

    def open(self, **extra):
        """A new Inbox on the same state, as a restart (with another configuration if one is given)."""
        self.box = Inbox(self.state, {**CFG, **extra}, self.adapter, clock=lambda: self.now)

    def record(self, mid, account, key, part=1, parts=1, text='', reply_to=None, **more):
        return {'msgid': mid, 'at': dt.datetime.fromtimestamp(self.now, dt.timezone.utc).isoformat(),
                'account': account, 'target': '#alpha', 'text': text, 'tags': tags(['helper'], key, part, parts),
                'reply_to': reply_to, **more}

    def ask(self, mid='q1', request='job-17', key='question'):
        self.adapter.messages.append(self.record(mid, 'alpha-manager', key,
                                                 text=f'[question {request}] Which approved default?'))
        return self.box.poll()['items']

    def reply(self, mid, account, part=1, parts=1, request='job-17', reply_to='q1', key='resolution', text=None,
              **more):
        return self.record(mid, account, key, part, parts, text or f'[decision {request}] The owner settled this',
                           reply_to, **more)

    def query(self, sql, args=()):
        with contextlib.closing(sqlite3.connect(self.state / 'inbox.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args)]

    def status(self, ident='q1'):
        return self.query('SELECT status FROM items WHERE id=?', (ident,))[0]['status']

    def replays(self):
        return [json.loads(row['reason']) for row in
                self.query("SELECT reason FROM audit WHERE operation='owner_replay' ORDER BY rowid")]


class OwnerAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def bench(self, name, **extra):
        return Bench(self.temp.name, name, **extra)

    def test_the_first_reply_resolves_once_and_the_other_account_is_retained_replay_evidence(self):
        for first, second in (('pat', 'quill'), ('quill', 'pat')):
            for arrival in ('one read', 'later read'):
                with self.subTest(first=first, second=second, arrival=arrival):
                    b = self.bench(f'{first}-{arrival[:3]}', owner_accounts=['quill'])
                    self.assertEqual([i['status'] for i in b.ask()], ['new'])
                    b.adapter.messages.append(b.reply('r1', first, nick=f'{first}-laptop'))
                    if arrival == 'later read':
                        self.assertEqual(b.box.poll()['items'], [])
                        self.assertEqual((b.status(), b.replays()), ('resolved', []))
                    b.adapter.messages.append(b.reply('r2', second, nick=f'{second}-phone'))
                    self.assertEqual(b.box.poll()['items'], [])
                    self.assertEqual(b.status(), 'resolved')
                    expected = {'selected': {'account': first, 'messages': [
                                    {'msgid': 'r1', 'nick': f'{first}-laptop', 'attested_account': first}]},
                                'replay': {'account': second, 'messages': [
                                    {'msgid': 'r2', 'nick': f'{second}-phone', 'attested_account': second}]}}
                    self.assertEqual(b.replays(), [expected])
                    # a repeated read, and a restart, record nothing more and send nothing
                    b.box.poll()
                    b.open(owner_accounts=['quill'])
                    b.box.poll()
                    self.assertEqual(b.replays(), [expected])
                    self.assertEqual(b.adapter.sends, [])
                    self.assertEqual(b.query("SELECT msgid, account FROM messages WHERE msgid IN ('r1','r2') "
                                             "ORDER BY msgid"),
                                     [{'msgid': 'r1', 'account': first}, {'msgid': 'r2', 'account': second}])
        with self.subTest(case='a late conflicting copy and a later reply cannot change what was selected'):
            b = self.bench('late', owner_accounts=['quill'])
            b.ask()
            b.adapter.messages += [b.reply('r1', 'pat'), b.reply('r2', 'quill')]
            b.box.poll()
            [recorded] = b.replays()
            self.assertEqual(recorded['selected']['account'], 'pat')
            # pat's group now conflicts (same key, other text), then pat answers again under another key
            b.adapter.messages.append(b.reply('r3', 'pat', text='[decision job-17] A different conclusion'))
            b.box.poll()
            b.adapter.messages.append(b.reply('r4', 'pat', key='resolution-later'))
            b.box.poll()
            b.open(owner_accounts=['quill'])
            b.box.poll()
            self.assertEqual((b.status(), b.replays(), b.adapter.sends), ('resolved', [recorded], []))
        with self.subTest(case='the attested spelling is kept beside the comparison form'):
            b = self.bench('mixed', owner_accounts=['quill'])
            b.ask()
            b.adapter.messages += [b.reply('r1', 'PAT', nick='Pat'), b.reply('r2', 'Quill')]
            b.box.poll()
            self.assertEqual(b.status(), 'resolved')
            self.assertEqual(b.replays(), [{
                'selected': {'account': 'pat', 'messages': [{'msgid': 'r1', 'nick': 'Pat', 'attested_account': 'PAT'}]},
                'replay': {'account': 'quill', 'messages': [
                    {'msgid': 'r2', 'nick': None, 'attested_account': 'Quill'}]}}])
            stored = {r['msgid']: r for r in b.query("SELECT msgid, account, payload FROM messages")}
            self.assertEqual((stored['r1']['account'], json.loads(stored['r1']['payload'])['attested_account']),
                             ('pat', 'PAT'))
            # an already lowercase account has the payload it always had
            self.assertNotIn('attested_account', json.loads(stored['q1']['payload']))
        with self.subTest(case='a message stored without the attested spelling is still the same message'):
            b = self.bench('stored-before', owner_accounts=['quill'])
            b.ask()
            b.adapter.messages.append(b.reply('r1', 'Quill'))
            b.box.poll()
            with contextlib.closing(sqlite3.connect(b.state / 'inbox.sqlite3')) as db:
                before = json.loads(db.execute("SELECT payload FROM messages WHERE msgid='r1'").fetchone()[0])
                del before['attested_account']
                db.execute("UPDATE messages SET payload=? WHERE msgid='r1'", (json.dumps(before, sort_keys=True),))
                db.execute('DELETE FROM cursors')  # every record is read again
                db.commit()
            b.box.poll()
            self.assertEqual(b.status(), 'resolved')
        with self.subTest(case='a repeat from the same account is not a second owner'):
            b = self.bench('same-account', owner_accounts=['quill'])
            b.ask()
            b.adapter.messages += [b.reply('r1', 'quill'), b.reply('r2', 'quill')]
            b.box.poll()
            self.assertEqual((b.status(), b.replays()), ('resolved', []))

    def test_incomplete_and_cross_account_multipart_replies_never_resolve(self):
        with self.subTest(case='one account, both parts'):
            b = self.bench('own-parts', owner_accounts=['quill'])
            b.ask()
            b.adapter.messages.append(b.reply('r1', 'quill', part=1, parts=2))
            result = b.box.poll()
            self.assertEqual((b.status(), result['items'][0]['claimable']), ('new', False))
            self.assertTrue(result['problems'])
            b.adapter.messages.append(b.reply('r2', 'quill', part=2, parts=2))
            self.assertEqual((b.box.poll()['items'], b.status()), ([], 'resolved'))
        with self.subTest(case='fragments of two owner accounts are not spliced'):
            b = self.bench('spliced', owner_accounts=['quill'])
            b.ask()
            b.adapter.messages += [b.reply('r1', 'pat', part=1, parts=2), b.reply('r2', 'quill', part=2, parts=2)]
            b.box.poll()
            self.assertEqual(b.status(), 'evidence_conflict')
            b.adapter.messages.append(b.reply('r3', 'pat', part=2, parts=2))
            b.box.poll()
            self.assertNotEqual(b.status(), 'resolved')
            self.assertEqual((b.replays(), b.adapter.sends), ([], []))
        with self.subTest(case='without the key the second account resolves nothing'):
            b = self.bench('legacy-quill')
            b.ask()
            b.adapter.messages.append(b.reply('r1', 'quill'))
            self.assertEqual(len(b.box.poll()['items']), 1)
            self.assertEqual((b.status(), b.replays()), ('new', []))

    def test_an_owner_mention_is_refused_and_either_accounts_evidence_blocks_a_send(self):
        b = self.bench('guards', owner_accounts=['quill'])
        b.ask()
        token = b.box.claim('q1')['claim']
        for name in ('pat', 'quill', 'Quill', 'PAT'):
            for answer in (f'please ask @{name} to decide', f'quoted `@{name}`'):
                with self.subTest(name=name, answer=answer):
                    with self.assertRaises(Blocked):
                        b.box.prepare('q1', token, {**DECISION, 'answer': answer})
        # whole identifiers only: a lookalike is an ordinary name
        b.box.prepare('q1', token, {**DECISION, 'answer': 'ask @quill2 or @patricia'})
        b.adapter.messages.append(b.reply('r1', 'quill', part=1, parts=2))
        with self.assertRaises(Blocked):
            b.box.send('q1', token)
        self.assertEqual(b.adapter.sends, [])
        with self.subTest(case='key absent: quill is only a name, and its post is not owner evidence'):
            legacy = self.bench('guards-legacy')
            legacy.ask()
            legacy_token = legacy.box.claim('q1')['claim']
            legacy.box.prepare('q1', legacy_token, {**DECISION, 'answer': 'ask @quill'})
            legacy.adapter.messages.append(legacy.reply('r1', 'quill', part=1, parts=2))
            legacy.box.send('q1', legacy_token)
            self.assertEqual(len(legacy.adapter.sends), 1)

    def test_configuration_is_read_whole_and_legacy_state_keeps_its_single_owner(self):
        for value in ('quill', 'pat,quill', {'quill': 1}, ['quill', 7], ['quill', None], [['quill']], ['qu ill'],
                      [''], ['sam'], ['SAM'], None, True, 7):
            with self.subTest(owner_accounts=value):
                with self.assertRaises(Blocked):
                    config({**CFG, 'owner_accounts': value})
        absent = Inbox(Path(self.temp.name) / 'unused', CFG, FakeAdapter()).digest()
        for value in ([], ['pat'], ['PAT', 'pat']):  # adds nothing: the digest and the pinned context are unchanged
            with self.subTest(adds_nothing=value):
                self.assertNotIn('owner_accounts', config({**CFG, 'owner_accounts': value}))
                self.assertEqual(Inbox(Path(self.temp.name) / 'unused', {**CFG, 'owner_accounts': value},
                                       FakeAdapter()).digest(), absent)
        listed = Inbox(Path(self.temp.name) / 'unused', {**CFG, 'owner_accounts': ['Quill', 'quill', 'pat']},
                       FakeAdapter())
        self.assertEqual((listed.cfg['owner_accounts'], listed.digest() == absent), (['quill'], False))

        legacy = self.bench('legacy')
        legacy.ask()
        context = json.loads(legacy.query('SELECT context FROM groups')[0]['context'])
        self.assertEqual(context, {'assistant': 'sam', 'owner': 'pat', 'alias': 'helper', 'manager': 'alpha-manager',
                                   'recipient': 'alpha-lead'})
        # the key arrives while a legacy question is pending: nothing is reset, and nothing is silently re-pinned
        legacy.open(owner_accounts=['quill'])
        with self.assertRaises(Blocked):
            legacy.box.poll()
        with self.assertRaises(Blocked):
            legacy.box.repin_config('operator approved a second owner account')
        legacy.open()  # the legacy configuration still works on the same state; quill is not an owner there
        legacy.adapter.messages.append(legacy.reply('r1', 'quill'))
        self.assertEqual(len(legacy.box.poll()['items']), 1)
        legacy.adapter.messages.append(legacy.reply('r2', 'pat'))
        self.assertEqual((legacy.box.poll()['items'], legacy.status()), ([], 'resolved'))
        # once it is resolved the key can be repinned; new questions pin it and the old one keeps its owner
        legacy.open(owner_accounts=['quill'])
        legacy.box.repin_config('operator approved a second owner account')
        legacy.ask('q2', 'job-18', 'question-two')
        context = json.loads(legacy.query("SELECT g.context FROM groups g JOIN items i USING(gid) WHERE i.id='q2'")[0]['context'])
        self.assertEqual(context['owner_accounts'], ['quill'])
        legacy.adapter.messages.append(legacy.reply('r3', 'quill', request='job-18', reply_to='q2', key='resolution-two'))
        self.assertEqual((legacy.box.poll()['items'], legacy.status('q2')), ([], 'resolved'))
        # and a pending question pinned with the account refuses a configuration that drops it
        legacy.ask('q3', 'job-19', 'question-three')
        legacy.open()
        with self.assertRaises(Blocked):
            legacy.box.repin_config('operator withdrew the second owner account')


if __name__ == '__main__':
    unittest.main()
