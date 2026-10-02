import contextlib
from copy import deepcopy
import datetime as dt
import fcntl
import io
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from routine_inbox.adapter import AdapterError, FakeAdapter, SendResult
from routine_inbox.inbox import Blocked, Inbox, config
from routine_inbox.__main__ import main
from routine_inbox.protocol import envelope, header, recipients, tags


CFG = {
    'alias': 'helper', 'assistant': 'assistant', 'owner': 'operator', 'claim_seconds': 600,
    'projects': {
        'alpha': {'channel': '#alpha', 'prefix': '#a-', 'request_prefix': 'alpha-',
                  'manager': 'a-manager', 'recipient': 'alpha-lead'},
        'beta': {'prefix': '#b-', 'request_prefix': 'beta-', 'manager': 'b-manager', 'recipient': 'beta-lead'},
    },
}
DECISION = {'kind': 'settled', 'answer': 'Use the approved default', 'source': 'approved issue 17 criterion 2'}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1800000000.0
        self.cfg = deepcopy(CFG)
        self.adapter = FakeAdapter()
        self.state = self.root / 'state'
        self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
        self.box.initialize()
        self.now += 1
        self.configfile = self.root / 'routing.json'
        self.configfile.write_text(json.dumps(self.cfg))
        for owner, name in ((socket, 'socket'), (subprocess, 'run'), (subprocess, 'Popen')):
            guard = patch.object(owner, name, side_effect=AssertionError('live I/O forbidden'))
            guard.start(); self.addCleanup(guard.stop)

    def record(self, mid='q1', key='question', part=1, parts=1, account='a-manager',
               target='#alpha', text=None, request='job-17', reply_to=None, route=True, auto=None):
        wire = tags(['helper'], key, part, parts, auto) if route else {}
        return {'msgid': mid, 'at': dt.datetime.fromtimestamp(self.now, dt.timezone.utc).isoformat(),
                'account': account, 'target': target, 'text': text or f'[question {request}] Which approved default?',
                'tags': wire, 'reply_to': reply_to}

    def query(self, sql, args=()):
        with contextlib.closing(sqlite3.connect(self.state / 'inbox.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args)]

    def item(self, ident='q1'):
        return self.query('SELECT * FROM items WHERE id=?', (ident,))[0]

    def ready(self, **kwargs):
        self.adapter.messages.append(self.record(**kwargs))
        return self.box.poll()['items'][0]['id']

    def prepare(self, ident='q1', kind='settled'):
        token = self.box.claim(ident)['claim']
        outgoing = self.box.prepare(ident, token, {**DECISION, 'kind': kind})
        return token, outgoing

    def send(self, ident='q1', kind='settled', outcome='sent', proof=''):
        token, outgoing = self.prepare(ident, kind)
        self.adapter.send_result = SendResult(outcome, proof)
        self.box.send(ident, token)
        return outgoing

    def echo(self, outgoing, mid='r1', part=1, parts=1, **updates):
        wire = {**outgoing['tags'], '+plateia/part': str(part), '+plateia/parts': str(parts)}
        return {**self.record(mid, account='assistant', target=outgoing['target'], text=outgoing['text'],
                              reply_to=wire['+draft/reply']), 'tags': wire, **updates}

    def ack(self, mid='r1', account='a-manager', target='#alpha'):
        self.adapter.acknowledgements.append({'msgid': mid, 'at': self.record()['at'], 'account': account, 'target': target})

    def resolution(self, mid='d1', account='a-manager', part=1, parts=1, reply_to='q1', **updates):
        return {**self.record(mid, key='resolution', part=part, parts=parts, account=account,
                              text='[decision job-17] The owner settled this', reply_to=reply_to), **updates}

    def cli(self, *args, state=None, configfile=None, adapter=True):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(['--config', str(configfile or self.configfile), '--state', str(state or self.state), *args],
                        adapter=self.adapter if adapter else None)

    def snapshot(self, state=None):
        path = state or self.state
        return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()} if path.exists() else {}

    def test_initial_cutover_does_not_enroll_history(self):
        self.adapter.messages.append(self.record() | {'at': '2020-01-01T00:00:00Z'})
        self.assertEqual(self.box.poll()['items'], [])
        with self.assertRaises(Blocked): self.box.initialize()

    def test_replay_and_restart_preserve_one_item(self):
        self.ready()
        self.adapter.messages.append(deepcopy(self.adapter.messages[0]))
        self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
        self.assertEqual(len(self.box.poll()['items']), 1)
        self.assertEqual(len(self.query('SELECT * FROM messages')), 1)

    def test_interruption_before_cursor_advance_rolls_back_admission(self):
        self.adapter.messages.append(self.record())
        with patch.object(self.box, 'advance_cursors', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError): self.box.poll()
        self.assertEqual(self.query('SELECT * FROM items'), [])
        self.assertEqual(self.query('SELECT * FROM cursors'), [])
        self.assertEqual(len(self.box.poll()['items']), 1)

    def test_multipart_admitted_once_after_different_poll_runs(self):
        self.adapter.messages.append(self.record(parts=2))
        first = self.box.poll()
        self.assertEqual(first['items'], []); self.assertEqual(first['incomplete'], 1)
        self.adapter.messages.append(self.record('q2', part=2, parts=2, text='… [question job-17] more context'))
        self.assertEqual(self.box.poll()['items'][0]['id'], 'q1')
        self.assertEqual(len(self.box.poll()['items']), 1)

    def test_nonmanager_spoof_missing_account_and_cross_project_rejected(self):
        self.adapter.messages += [
            self.record('worker', account='a-worker'),
            self.record('spoof', account='outsider', text='[question job-17] I am a-manager'),
            self.record('nick', account=None) | {'nick': 'a-manager'},
            self.record('cross', account='a-manager', target='#b-work'),
            self.record('self', account='assistant'),
            self.record('wrong-request', request='beta-17'),
        ]
        self.assertEqual(self.box.poll()['items'], [])

    def test_legitimate_nonmatching_request_prefix_is_allowed(self):
        self.assertEqual(self.ready(request='job-17'), 'q1')

    def test_invalid_envelope_is_not_question_or_acceptance(self):
        self.adapter.messages.append(self.record() | {'tags': {'+plateia/to': 'helper'}})
        self.assertEqual(self.box.poll()['items'], [])
        self.adapter.messages.append(self.record('q2', key='question2'))
        self.box.poll()
        outgoing = self.send('q2')
        self.adapter.messages += [self.echo(outgoing), self.record('bad-ack', reply_to='r1') | {'tags': {'+plateia/key': 'broken'}}]
        self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')

    def test_incidental_and_quoted_mentions_do_not_enroll(self):
        for n, text in enumerate(('[question job-17] Prior @helper example', '[question job-17] `@helper`',
                                 '[question job-17] > @helper', '… [question job-17] @helper')):
            self.adapter.messages.append(self.record(str(n), route=False, text=text))
        self.assertEqual(self.box.poll()['items'], [])

    def test_legacy_quarantine_and_structured_resend(self):
        self.adapter.messages.append(self.record('old', route=False, text='[question] @helper choose?'))
        row = self.box.poll()['items'][0]
        self.assertEqual(row['status'], 'quarantined')
        with self.assertRaises(Blocked): self.box.claim('old')
        self.adapter.messages.append(self.record())
        self.assertEqual(self.box.poll()['items'][0]['id'], 'q1')

    def test_metadata_and_content_conflicts_block_without_cursor_loss(self):
        self.ready()
        before = self.query('SELECT * FROM cursors')
        self.adapter.messages.append(self.record('other', text='[question job-17] Conflicting text'))
        result = self.box.poll()
        self.assertEqual(result['items'][0]['status'], 'quarantined')
        self.assertTrue(result['problems'])
        self.assertNotEqual(self.query('SELECT * FROM cursors'), before)
        self.assertEqual(len(self.query('SELECT * FROM items')), 1)

    def test_ack_before_delivery_survives_cursor_advance(self):
        self.ready(); outgoing = self.send()
        self.ack(); self.box.poll()
        self.adapter.messages.append(self.echo(outgoing))
        self.assertEqual(self.box.poll()['items'], [])
        self.assertEqual(self.item()['status'], 'accepted')

    def test_send_uses_original_target_and_explicit_manager_recipient(self):
        self.ready(target='#a-work'); outgoing = self.send()
        outgoing = self.adapter.sends[0]
        self.assertEqual(outgoing['target'], '#a-work')
        self.assertEqual(outgoing['tags']['+plateia/key'], self.item()['action'])
        self.assertEqual(outgoing['tags']['+plateia/to'], 'alpha-lead')
        self.assertEqual(outgoing['tags']['+draft/reply'], 'q1')
        self.assertEqual(outgoing['tags']['+plateia/auto'], 'settled')
        self.assertIn('[answer job-17] Automatic routine clarification (settled):', outgoing['text'])
        self.assertIn('No new scope, solve, merge, permission, credential or security authority.', outgoing['text'])
        self.assertEqual(self.item()['status'], 'submitted')

    def test_submitted_then_delivered_then_accepted_by_reply(self):
        self.ready(); outgoing = self.send()
        self.adapter.messages.append(self.echo(outgoing))
        self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')
        self.adapter.messages.append(self.record('ack', route=False, text='[status job-17] accepted', reply_to='r1'))
        self.assertEqual(self.box.poll()['items'], [])

    def test_unrelated_or_wrong_author_acceptance_never_counts(self):
        self.ready(); outgoing = self.send()
        self.adapter.messages += [self.echo(outgoing), self.record('status', route=False, text='[status job-17] working')]
        for who in ('a-worker', None, 'b-manager'):
            self.ack(account=who)
        self.adapter.messages.append(self.record('reply', account='outsider', reply_to='r1'))
        self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')

    def test_every_part_of_one_copy_needs_acceptance(self):
        self.ready(); outgoing = self.send()
        self.adapter.messages += [self.echo(outgoing, parts=2), self.echo(outgoing, 'r2', part=2, parts=2)]
        self.ack(); self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')
        self.ack('r2'); self.assertEqual(self.box.poll()['items'], [])

    def test_retry_copies_never_mix_accepted_parts(self):
        self.ready(); outgoing = self.send()
        self.adapter.messages += [self.echo(outgoing, 'a1', parts=2), self.echo(outgoing, 'a2', part=2, parts=2),
                                  self.echo(outgoing, 'b1', parts=2), self.echo(outgoing, 'b2', part=2, parts=2)]
        self.ack('a1'); self.ack('b2')
        self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')
        self.ack('b1'); self.assertEqual(self.box.poll()['items'], [])

    def test_interleaved_or_conflicting_copies_are_not_delivery(self):
        self.ready(); outgoing = self.send()
        self.adapter.messages += [self.echo(outgoing, 'a1', parts=2), self.echo(outgoing, 'b1', parts=2),
                                  self.echo(outgoing, 'a2', part=2, parts=2)]
        self.assertEqual(self.box.poll()['items'][0]['status'], 'evidence_conflict')

    def test_escalation_waits_for_owner_resolution(self):
        self.ready(); outgoing = self.send(kind='escalate')
        self.adapter.messages.append(self.echo(outgoing))
        self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')
        self.ack()
        self.assertEqual(self.box.poll()['items'][0]['status'], 'waiting_owner')
        self.adapter.messages.append(self.resolution(account='operator'))
        self.assertEqual(self.box.poll()['items'], [])
        self.assertEqual(self.item()['status'], 'resolved')

    def test_resolution_transitions_from_each_nonterminal_state(self):
        for state in ('new', 'claimed', 'sending', 'send_failed', 'uncertain', 'submitted', 'delivered', 'waiting_owner'):
            with self.subTest(state=state):
                self.state = self.root / ('case-' + state)
                self.adapter = FakeAdapter()
                self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
                self.box.initialize(); self.now += 1; self.ready()
                if state == 'claimed': self.prepare()
                elif state == 'sending':
                    token, _ = self.prepare(); self.adapter.send_result = KeyboardInterrupt()
                    with self.assertRaises(KeyboardInterrupt): self.box.send('q1', token)
                elif state in ('send_failed', 'uncertain', 'submitted', 'delivered', 'waiting_owner'):
                    outcome = {'send_failed': 'failed', 'uncertain': 'uncertain'}.get(state, 'sent')
                    outgoing = self.send(kind='escalate' if state == 'waiting_owner' else 'settled',
                                         outcome=outcome, proof='positive fake proof')
                    if state in ('delivered', 'waiting_owner'):
                        self.adapter.messages.append(self.echo(outgoing))
                        if state == 'waiting_owner': self.ack()
                        self.box.poll()
                self.assertEqual(self.item()['status'], state)
                sends = len(self.adapter.sends)
                self.adapter.messages.append(self.resolution())
                self.box.poll()
                self.assertEqual(self.item()['status'], 'resolved')
                self.assertEqual(len(self.adapter.sends), sends)

    def test_reply_filter_cannot_stitch_around_missing_auto_or_invalid_envelope(self):
        for broken in ('+plateia/auto', '+plateia/part'):
            with self.subTest(broken=broken):
                self.state = self.root / ('barrier-' + broken.rsplit('/', 1)[1])
                self.adapter = FakeAdapter(); self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
                self.box.initialize(); self.now += 1; self.ready(); outgoing = self.send(outcome='uncertain')
                bad = self.echo(outgoing, 'b1', parts=2); bad['tags'].pop(broken)
                self.adapter.messages += [self.echo(outgoing, 'a1', parts=2), bad,
                                          self.echo(outgoing, 'b2', part=2, parts=2)]
                self.ack('a1'); self.ack('b2')
                result = self.box.poll()
                self.assertEqual(result['items'][0]['status'], 'evidence_conflict')
                self.assertTrue(result['problems'])

    def test_mixed_kind_and_recipient_questions_are_quarantined(self):
        for change in ('kind', 'recipient'):
            with self.subTest(change=change):
                self.state = self.root / ('framing-' + change)
                self.adapter = FakeAdapter(); self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
                self.box.initialize(); self.now += 1
                first = self.record(parts=2, text='[blocked job-17] Trouble' if change == 'kind' else None)
                second = self.record('q2', part=2, parts=2)
                if change == 'recipient': second['tags']['+plateia/to'] = 'other-helper'
                self.adapter.messages += [first, second]
                result = self.box.poll()
                self.assertEqual(result['items'][0]['status'], 'quarantined')
                self.assertFalse(result['items'][0]['claimable'])
                with self.assertRaises(Blocked): self.box.claim('q1')

    def test_invalid_or_nonquestion_fragment_never_disappears_from_assembly(self):
        for kind in ('status', 'bad-envelope'):
            with self.subTest(kind=kind):
                self.state = self.root / ('question-barrier-' + kind)
                self.adapter = FakeAdapter(); self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
                self.box.initialize(); self.now += 1
                middle = self.record('middle', part=1, parts=2, text='[status job-17] different')
                if kind == 'bad-envelope': middle['tags'].pop('+plateia/part')
                self.adapter.messages += [self.record(parts=2), middle, self.record('last', part=2, parts=2)]
                result = self.box.poll()
                self.assertEqual(result['items'][0]['status'], 'quarantined')
                self.assertFalse(result['items'][0]['claimable'])

    def test_headerless_direct_questions_are_visible_quarantine(self):
        self.adapter.messages += [self.record('one', route=False, text='@helper Which default?'),
                                  self.record('two', route=False, text='mgr: helper: Which default?')]
        rows = self.box.poll()['items']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['status'] == 'quarantined' for row in rows))

    def test_expiry_during_outgoing_prevents_adapter_invocation(self):
        self.ready(); token, _ = self.prepare(); original = self.box.outgoing
        def expire(row):
            outgoing = original(row); self.now += 601; return outgoing
        with patch.object(self.box, 'outgoing', side_effect=expire):
            with self.assertRaises(Blocked): self.box.send('q1', token)
        self.assertEqual(self.adapter.sends, [])
        self.assertEqual(self.box.poll()['items'][0]['status'], 'uncertain')

    def test_late_conflicting_copy_reopens_acceptance_as_visible_conflict(self):
        for kind in ('settled', 'escalate'):
            with self.subTest(kind=kind):
                self.state = self.root / ('late-' + kind)
                self.adapter = FakeAdapter(); self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
                self.box.initialize(); self.now += 1; self.ready(); outgoing = self.send(kind=kind)
                self.adapter.messages.append(self.echo(outgoing)); self.ack(); self.box.poll()
                self.assertEqual(self.item()['status'], 'accepted' if kind == 'settled' else 'waiting_owner')
                self.adapter.messages.append(self.echo(outgoing, 'conflict', text='[answer job-17] different'))
                result = self.box.poll()
                self.assertEqual(result['items'][0]['status'], 'evidence_conflict')
                self.assertTrue(result['problems'])
                self.assertEqual(self.box.poll()['items'][0]['status'], 'evidence_conflict')

    def test_unrecognized_attested_account_does_not_block_other_questions(self):
        for n, account in enumerate(('bob|away', 'alice[m]', 'bystanderé')):
            self.adapter.messages.append(self.record('bystander-' + str(n), account=account))
        self.assertEqual(self.ready(), 'q1')

    def test_repin_lifetime_preserves_uncertain_action_and_records_audit(self):
        self.ready(); self.send(outcome='uncertain'); action = self.item()['action']
        proposed = {**self.cfg, 'claim_seconds': 900}
        box = Inbox(self.state, proposed, self.adapter, clock=lambda: self.now)
        with self.assertRaises(Blocked): box.poll()
        box.repin_config('operator approves longer claims')
        self.assertEqual(box.poll()['items'][0]['status'], 'uncertain')
        self.assertEqual(self.item()['action'], action)
        self.assertEqual(self.query("SELECT operation FROM audit WHERE operation='repin_config'")[0]['operation'], 'repin_config')
        with self.assertRaises(Blocked): box.claim('q1')

    def test_repin_checks_unread_obligations_before_manager_change(self):
        self.adapter.messages.append(self.record())
        proposed = deepcopy(self.cfg); proposed['projects']['alpha']['manager'] = 'new-manager'
        box = Inbox(self.state, proposed, self.adapter, clock=lambda: self.now)
        with self.assertRaises(Blocked): box.repin_config('operator requested rotation')
        self.assertEqual(self.box.poll()['items'][0]['id'], 'q1')

    def test_repin_added_project_starts_fresh_without_dropping_existing_work(self):
        self.ready()
        old = self.record('old-gamma', account='g-manager', target='#g-work', request='gamma-1')
        self.now += 100
        proposed = deepcopy(self.cfg)
        proposed['projects']['gamma'] = {'prefix': '#g-', 'request_prefix': 'gamma-',
                                         'manager': 'g-manager', 'recipient': 'g-manager'}
        box = Inbox(self.state, proposed, self.adapter, clock=lambda: self.now)
        box.repin_config('operator adds gamma')
        self.adapter.messages += [old, self.record('new-gamma', key='new', account='g-manager', target='#g-work', request='gamma-2')]
        self.assertEqual({i['id'] for i in box.poll()['items']}, {'q1', 'new-gamma'})

    def test_repin_manager_rotation_after_terminal_item_keeps_history(self):
        self.ready(); outgoing = self.send(); self.adapter.messages.append(self.echo(outgoing)); self.ack(); self.box.poll()
        proposed = deepcopy(self.cfg); proposed['projects']['alpha']['manager'] = 'new-manager'
        box = Inbox(self.state, proposed, self.adapter, clock=lambda: self.now)
        box.repin_config('operator rotates manager after completion')
        self.adapter.messages.append(self.record('new-question', key='new', account='new-manager'))
        self.assertEqual(box.poll()['items'][0]['id'], 'new-question')
        self.assertEqual(self.item()['status'], 'accepted')

    def test_partial_resolution_is_visible_and_not_repeatedly_claimed(self):
        self.ready(); self.adapter.messages.append(self.resolution() | {'tags': {}})
        result = self.box.poll()
        self.assertFalse(result['items'][0]['claimable']); self.assertTrue(result['problems'])
        with self.assertRaises(Blocked): self.box.claim('q1')

    def test_config_repin_command_is_explicit_and_preserves_existing_items(self):
        self.ready()
        self.configfile.write_text(json.dumps({**self.cfg, 'claim_seconds': 900}))
        self.assertEqual(self.cli('poll'), 75)
        self.assertEqual(self.cli('repin-config', '--reason', 'operator approved duration'), 0)
        self.assertEqual(self.cli('poll'), 0)
        self.assertEqual(self.item()['status'], 'new')

    def test_poll_does_not_publish_claim_bearer_token(self):
        self.ready(); self.prepare()
        self.assertNotIn('claim', self.box.poll()['items'][0])

    def test_reinitialization_never_mutates_an_older_database(self):
        state = self.root / 'older'; state.mkdir()
        with contextlib.closing(sqlite3.connect(state / 'inbox.sqlite3')) as db:
            db.execute('CREATE TABLE legacy(value TEXT)'); db.commit()
        before = self.snapshot(state)
        with self.assertRaises(Blocked): Inbox(state, self.cfg, self.adapter).initialize()
        self.assertEqual(self.snapshot(state), before)

    def test_invalid_resolution_cannot_close_question(self):
        self.ready()
        self.adapter.messages += [self.resolution('bad-author', account='outsider'), self.resolution('wrong-root', reply_to='another')]
        self.assertEqual(self.box.poll()['items'][0]['status'], 'new')

    def test_resolution_before_prepare_or_send_suppresses_answer(self):
        self.ready(); token, _ = self.prepare()
        self.adapter.messages.append(self.resolution())
        with self.assertRaises(Blocked): self.box.send('q1', token)
        self.assertEqual(self.adapter.sends, [])
        # A blocked send rolls back its reconciliation; a read-only poll records it.
        self.box.poll(); self.assertEqual(self.item()['status'], 'resolved')

    def test_partial_resolution_blocks_send_until_complete(self):
        self.ready(); token, _ = self.prepare()
        self.adapter.messages.append(self.resolution(parts=2))
        with self.assertRaises(Blocked): self.box.send('q1', token)
        self.assertEqual(self.adapter.sends, [])
        self.adapter.messages.append(self.resolution('d2', part=2, parts=2))
        self.assertEqual(self.box.poll()['items'], [])

    def test_overlapping_claimants_and_expired_token(self):
        self.ready(); old = self.box.claim('q1')['claim']
        with self.assertRaises(Blocked): self.box.claim('q1')
        self.now += 601
        fresh = self.box.claim('q1')['claim']
        with self.assertRaises(Blocked): self.box.prepare('q1', old, DECISION)
        self.box.prepare('q1', fresh, DECISION)
        with self.assertRaises(Blocked): self.box.send('q1', old)
        self.box.send('q1', fresh)

    def test_expired_prepared_claim_can_be_reconsidered_without_changing_key(self):
        self.ready(); token, first = self.prepare()
        self.now += 601
        with self.assertRaises(Blocked): self.box.send('q1', token)
        new = self.box.claim('q1')['claim']
        second = self.box.prepare('q1', new, {**DECISION, 'source': 'rechecked approved issue'})
        self.assertEqual(first['tags']['+plateia/key'], second['tags']['+plateia/key'])

    def test_crash_after_send_intent_never_automatically_retries(self):
        self.ready(); token, _ = self.prepare()
        self.adapter.send_result = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.box.send('q1', token)
        self.assertEqual(self.box.poll()['items'][0]['status'], 'sending')
        self.box = Inbox(self.state, self.cfg, self.adapter, clock=lambda: self.now)
        self.now += 601
        self.assertEqual(self.box.poll()['items'][0]['status'], 'uncertain')
        with self.assertRaises(Blocked): self.box.claim('q1')
        with self.assertRaises(Blocked): self.box.send('q1', token)
        self.assertEqual(len(self.adapter.sends), 1)

    def test_expiry_during_send_cannot_record_result(self):
        self.ready(); token, _ = self.prepare()
        def expires(**kwargs):
            self.now += 601
            return SendResult('sent')
        with patch.object(self.adapter, 'send', side_effect=expires):
            with self.assertRaises(Blocked): self.box.send('q1', token)
        self.assertEqual(self.item()['status'], 'sending')
        self.assertEqual(self.box.poll()['items'][0]['status'], 'uncertain')

    def test_uncertain_exception_and_unknown_outcomes_never_retry(self):
        self.ready(); token, _ = self.prepare()
        self.adapter.send_result = TimeoutError('ambiguous outcome')
        with self.assertRaises(TimeoutError): self.box.send('q1', token)
        self.assertEqual(self.item()['status'], 'uncertain')
        with self.assertRaises(Blocked): self.box.claim('q1')

    def test_sending_can_reconcile_delivery_without_recording_sender_result(self):
        self.ready(); token, outgoing = self.prepare()
        self.adapter.send_result = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.box.send('q1', token)
        self.adapter.messages.append(self.echo(outgoing))
        self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')

    def test_unknown_result_and_conflicting_complete_copies_remain_uncertain(self):
        self.ready(); outgoing = self.send(outcome='unknown')
        self.adapter.messages += [self.echo(outgoing, 'first'), self.echo(outgoing, 'second', text='[answer job-17] different')]
        self.ack('first'); self.ack('second')
        self.assertEqual(self.box.poll()['items'][0]['status'], 'evidence_conflict')

    def test_malformed_server_record_blocks_without_advancing_cursor(self):
        self.adapter.messages.append(self.record() | {'at': 'bad timestamp'})
        with self.assertRaises(AdapterError): self.box.poll()
        self.assertEqual(self.query('SELECT * FROM cursors'), [])

    def test_uncertain_becomes_delivered_only_on_complete_evidence(self):
        self.ready(); outgoing = self.send(outcome='uncertain')
        self.adapter.messages.append(self.echo(outgoing, parts=2))
        self.assertEqual(self.box.poll()['items'][0]['status'], 'uncertain')
        self.adapter.messages.append(self.echo(outgoing, 'r2', part=2, parts=2))
        self.assertEqual(self.box.poll()['items'][0]['status'], 'delivered')

    def test_proven_send_failure_allows_new_claim_and_same_action(self):
        self.ready(); first = self.send(outcome='failed', proof='fake rejected before any submission')
        self.assertEqual(self.item()['status'], 'send_failed')
        second = self.send()
        self.assertEqual(first, second)
        self.assertEqual(self.item()['status'], 'submitted')

    def test_unproven_failure_is_uncertain(self):
        self.ready(); self.send(outcome='failed')
        self.assertEqual(self.item()['status'], 'uncertain')

    def test_operator_resend_is_explicit_audited_and_preserves_key_and_content(self):
        self.ready(); first = self.send(outcome='uncertain')
        with self.assertRaises(Blocked): self.box.authorize_resend('q1', '')
        self.box.authorize_resend('q1', 'owner authorized this resend after inspection')
        token = self.box.claim('q1')['claim']
        with self.assertRaises(Blocked): self.box.prepare('q1', token, {**DECISION, 'answer': 'different'})
        second = self.box.prepare('q1', token, DECISION)
        self.assertEqual(first, second)
        self.assertEqual(len(self.query('SELECT * FROM audit')), 1)

    def test_invalid_reply_fields_and_owner_mentions_are_refused(self):
        self.ready(); token = self.box.claim('q1')['claim']
        for decision in ({**DECISION, 'source': ''}, {'kind': 'escalate', 'answer': '', 'source': 'evidence'},
                         {'kind': 'escalate', 'answer': 'choice', 'source': ''}, [],
                         {**DECISION, 'answer': 'quoted `@operator`'}):
            with self.assertRaises(Blocked): self.box.prepare('q1', token, decision)
        self.assertEqual(self.adapter.sends, [])

    def test_adapter_read_errors_rollback_both_cursors_and_item_state(self):
        self.ready(); outgoing = self.send()
        before = self.query('SELECT * FROM cursors')
        self.adapter.messages.append(self.echo(outgoing)); self.ack()
        self.adapter.ack_error = 'ack source unavailable'
        self.assertEqual(self.cli('poll'), 75)
        self.assertEqual(self.query('SELECT * FROM cursors'), before)
        self.assertEqual(self.item()['status'], 'submitted')
        self.adapter.ack_error = None; self.adapter.message_error = 'history replaced'
        self.assertEqual(self.cli('poll'), 75)
        self.assertEqual(self.query('SELECT * FROM cursors'), before)
        self.adapter.message_error = None
        self.assertEqual(self.box.poll()['items'], [])

    def test_poll_and_reconciliation_never_send(self):
        self.ready(); self.box.poll(); self.box.poll()
        self.assertEqual(self.adapter.sends, [])

    def test_incomplete_config_leaves_existing_and_absent_state_unchanged(self):
        before = self.snapshot(); self.configfile.write_text('{}')
        self.assertEqual(self.cli('poll'), 75); self.assertEqual(self.snapshot(), before)
        missing = self.root / 'absent'
        self.assertEqual(self.cli('poll', state=missing), 75); self.assertFalse(missing.exists())

    def test_identity_collision_and_ambiguous_targets_are_incomplete(self):
        for cfg in ({**self.cfg, 'assistant': 'operator'}, {**self.cfg, 'assistant': 'a-manager'},
                    {**self.cfg, 'projects': {**self.cfg['projects'], 'copy': {**self.cfg['projects']['alpha'], 'request_prefix': 'copy-'}}}):
            self.configfile.write_text(json.dumps(cfg)); before = self.snapshot()
            self.assertEqual(self.cli('poll'), 75); self.assertEqual(self.snapshot(), before)

    def test_overlap_existing_and_before_first_initialization_changes_nothing(self):
        for state in (self.state, self.root / 'first'):
            state.mkdir(exist_ok=True)
            with (state/'run.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                before = self.snapshot(state)
                self.assertEqual(self.cli('poll', state=state), 75)
                self.assertEqual(self.snapshot(state), before)

    def test_no_adapter_or_missing_state_never_initializes_silently(self):
        missing = self.root / 'absent'
        self.assertEqual(self.cli('poll', state=missing, adapter=False), 75)
        self.assertEqual(self.cli('poll', state=missing), 75)
        self.assertFalse(missing.exists())

    def test_manifest_change_is_blocked(self):
        box = Inbox(self.state, {**self.cfg, 'alias': 'other-helper'}, self.adapter)
        with self.assertRaises(Blocked): box.poll()

    def test_quiet_unchanged_poll(self):
        self.box.poll(); self.assertFalse(self.box.poll()['changed'])


class ProtocolTests(unittest.TestCase):
    def test_leading_address_forms_and_standard_nametags(self):
        for text in ('@helper hi', '[question job-1] mgr: @helper hi', '[question] helper: hi',
                     '[question job-1] solve/R2: @helper hi'):
            self.assertEqual(recipients(text, {'helper'}), {'helper'})
        self.assertEqual(header('… [question job-1] tail'), ('question', 'job-1', 'tail', True))

    def test_auto_marker_requires_complete_envelope_and_valid_kind(self):
        self.assertEqual(envelope(tags(['helper'], 'key', 1, 1, 'settled'))['auto'], 'settled')
        for raw in ({'+plateia/auto': 'settled'}, tags(['helper'], 'key', 1, 1) | {'+plateia/auto': 'grant'},
                    tags(['helper'], 'key', 1, 1) | {'+plateia/to': 7}):
            with self.assertRaises(ValueError): envelope(raw)


if __name__ == '__main__':
    unittest.main()
