import contextlib
import datetime as dt
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from routine_inbox.inbox import Blocked, Inbox
from routine_inbox.protocol import envelope, header, recipients, tags


class ProtocolTests(unittest.TestCase):
    def test_only_leading_addressee_routes(self):
        known = {"operator","helper","a-manager"}
        positives = {"@helper hi":{"helper"}, "[question job-1] mgr: @helper hi":{"helper"},
                     "[blocked job-1] solve/R2: @operator help":{"operator"},
                     "[question] operator: hi":{"operator"},
                     "[question] @HELPER @a-manager hi":{"helper","a-manager"}}
        for text, expected in positives.items():
            self.assertEqual(recipients(text,known),expected,text)
        for text in ('[answer] the earlier @operator question', '`@operator`',
                     '> @operator hi', '"@operator hi"', '[status] mgr: quoted: @operator',
                     '[question] @helper-long hi', '… [question] @operator incidental'):
            self.assertEqual(recipients(text,known),set(),text)

    def test_envelope_is_explicit_and_complete(self):
        self.assertIsNone(envelope({"+draft/reply":"msg"}))
        self.assertEqual(envelope(tags(["helper"],"batch",2,3))["part"],2)
        for raw in ({"+plateia/to":"helper"}, tags(["helper"],"batch",1,1)|{"+plateia/parts":"0"}):
            with self.assertRaises(ValueError):envelope(raw)

    def test_old_double_tags_and_continuation(self):
        self.assertEqual(header('… [question job-1] [question job-1] tail'),
                         ('question','job-1','tail',True))


class InboxTests(unittest.TestCase):
    def setUp(self):
        preflight=patch.object(Inbox,'verify_manager',return_value={'live':True})
        preflight.start();self.addCleanup(preflight.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = self.root/'logs'; self.logs.mkdir()
        (self.logs/'alpha.jsonl').touch()
        self.acks = self.root/'acks.jsonl';self.acks.touch()
        self.cfg = {'alias':'helper','assistant':'assistant','owner':'operator',
                    'projects':{'alpha':{'channel':'#alpha','prefix':'#a-','manager':'a-manager'}},
                    'logs':str(self.logs),'acks':str(self.acks),'pchat':'/example/pchat'}
        self.state = self.root/'state'
        self.box = Inbox(self.state,self.cfg)
        self.box.initialize()
        self.when = dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=2)

    def tearDown(self):
        self.box.db.close()

    def entry(self, mid='m1', *, part=1, parts=1, key='q1', sender='a-manager',
              text=None, channel='#alpha', reply_to=None, verified=True, routing=True):
        e={'at':self.when.isoformat(),'msgid':mid,'from':sender,'verified':verified,'channel':channel,
           'text':text or ('[question job-1] Which documented default?' if part==1 else '… [question job-1] more context')}
        if routing:e['routing']=envelope(tags(['helper'],key,part,parts))
        if reply_to:e['reply_to']=reply_to
        return e

    def append(self,*entries):
        for e in entries:
            with (self.logs/(e['channel'][1:]+'.jsonl')).open('a') as f:f.write(json.dumps(e)+'\n')

    def ready(self):
        self.append(self.entry())
        return self.box.poll()['items'][0]['id']

    def prepare(self,bid,kind='settled'):
        token=self.box.claim(bid)['claim']
        return self.box.prepare(bid,token,{'kind':kind,'answer':'Use the documented default',
                                         'source':'approved issue 17, acceptance criterion 2'})

    def echoed(self,bid,mid='reply1',part=1,parts=1):
        item=self.box.db.execute('SELECT * FROM items WHERE id=?',(bid,)).fetchone()
        return self.entry(mid,sender='assistant',text='[answer job-1] See approved issue 17',
                          key=item['action'],reply_to='m1',part=part,parts=parts)|{
                              'routing':envelope(tags(['a-manager'],item['action'],part,parts))}

    def ack(self,mid='reply1',by='a-manager'):
        with self.acks.open('a') as f:f.write(json.dumps({'reply_to':mid,'by':by,'channel':'#alpha','project':'alpha'})+'\n')

    def test_baseline_never_replays_history(self):
        self.box.db.close()
        state=self.root/'other'
        self.append(self.entry())
        other=Inbox(state,self.cfg)
        self.addCleanup(other.db.close)
        self.assertEqual(other.initialize()['historical_requests_enrolled'],0)
        self.assertEqual(other.poll()['items'],[])
        with self.assertRaises(Blocked):other.initialize()

    def test_restart_replay_and_duplicate_poll_keep_one_question(self):
        bid=self.ready()
        self.box.db.close();self.box=Inbox(self.state,self.cfg)
        self.append(self.entry()|{'replayed':True})
        self.assertEqual([x['id'] for x in self.box.poll()['items']],[bid])
        self.assertEqual(len(self.box.poll()['items']),1)

    def test_late_older_server_time_is_collected(self):
        self.ready()
        self.when-=dt.timedelta(seconds=1)
        self.append(self.entry('m2',key='q2'))
        self.assertEqual(len(self.box.poll()['items']),2)

    def test_new_request_channel_is_discovered(self):
        self.append(self.entry(channel='#a-job-1'))
        self.assertEqual(len(self.box.poll()['items']),1)

    def test_old_replay_in_new_channel_is_not_new_work(self):
        self.when-=dt.timedelta(days=1)
        self.append(self.entry(channel='#a-old'))
        self.assertEqual(self.box.poll()['items'],[])

    def test_unverified_or_worker_cannot_enroll_questions(self):
        self.append(self.entry('m1',verified=False),self.entry('m2',sender='a-worker'))
        self.assertEqual(self.box.poll()['items'],[])

    def test_incidental_or_quoted_legacy_mention_is_ignored(self):
        self.append(self.entry(routing=False,text='[question job-1] Earlier @helper example'))
        self.assertEqual(self.box.poll()['items'],[])

    def test_legacy_direct_question_retained_but_not_autoanswered(self):
        self.append(self.entry(routing=False,text='[question job-1] @helper choose?'))
        item=self.box.poll()['items'][0]
        self.assertFalse(item['claimable'])
        self.assertIn('unframed',item['problem'])
        with self.assertRaises(Blocked):self.box.claim(item['id'])

    def test_out_of_order_multiline_survives_restart(self):
        self.append(self.entry('m2',part=2,parts=2))
        item=self.box.poll()['items'][0]
        self.assertFalse(item['complete'])
        with self.assertRaises(Blocked):self.box.claim(item['id'])
        self.box.db.close();self.box=Inbox(self.state,self.cfg)
        self.append(self.entry(parts=2))
        item=self.box.poll()['items'][0]
        self.assertTrue(item['complete']);self.assertTrue(item['claimable'])
        self.assertEqual(item['root'],'m1')
        self.assertIn('more context',item['text'])

    def test_duplicate_transport_part_is_deduped(self):
        self.append(self.entry(),self.entry('duplicate'))
        self.assertEqual(len(self.box.poll()['items']),1)
        self.assertEqual(self.box.db.execute('SELECT count(*) FROM fragments').fetchone()[0],1)

    def test_conflicting_part_blocks_action(self):
        self.append(self.entry(),self.entry('different',text='[question job-1] Changed question'))
        item=self.box.poll()['items'][0]
        self.assertFalse(item['claimable']);self.assertIn('conflicts',item['problem'])

    def test_partial_line_keeps_cursor_before_record(self):
        raw=json.dumps(self.entry()).encode()
        path=self.logs/'alpha.jsonl';path.write_bytes(raw[:30])
        self.assertEqual(self.box.poll()['items'],[])
        with path.open('ab') as f:f.write(raw[30:]+b'\n')
        self.assertEqual(len(self.box.poll()['items']),1)

    def test_truncated_log_reports_problem_without_forgetting_pending(self):
        self.ready();(self.logs/'alpha.jsonl').write_text('')
        result=self.box.poll()
        self.assertEqual(len(result['items']),1);self.assertTrue(result['problems'])

    def test_single_run_and_item_claim_locks(self):
        bid=self.ready()
        with (self.state/'run.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(Blocked):self.box.poll()
        self.box.claim(bid)
        with self.assertRaises(Blocked):self.box.claim(bid)

    def test_uncertain_send_is_never_retried(self):
        bid=self.ready();self.prepare(bid)
        def fail(argv):raise TimeoutError('unknown send outcome')
        with self.assertRaises(TimeoutError):self.box.send(bid,fail)
        self.box.db.close();self.box=Inbox(self.state,self.cfg)
        with self.assertRaises(Blocked):self.box.send(bid,fail)
        self.assertEqual(self.box.poll()['items'][0]['status'],'uncertain')

    def test_outbox_exit_does_not_duplicate_send(self):
        bid=self.ready();self.prepare(bid)
        self.box.send(bid,lambda argv:subprocess.CompletedProcess(argv,3))
        with self.assertRaises(Blocked):self.box.send(bid,lambda argv:None)
        self.append(self.echoed(bid))
        self.assertEqual(self.box.poll()['items'][0]['status'],'awaiting_manager_ack')

    def test_delivery_and_unrelated_manager_post_are_not_acceptance(self):
        bid=self.ready();self.prepare(bid)
        self.box.send(bid,lambda argv:subprocess.CompletedProcess(argv,0))
        self.append(self.echoed(bid),self.entry('status',routing=False,text='[status job-1] working'))
        self.ack(by='a-worker')
        self.assertEqual(self.box.poll()['items'][0]['status'],'awaiting_manager_ack')
        self.ack();self.assertEqual(self.box.poll()['items'],[])

    def test_every_outgoing_fragment_needs_acceptance(self):
        bid=self.ready();self.prepare(bid)
        self.append(self.echoed(bid,parts=2),self.echoed(bid,'reply2',part=2,parts=2))
        self.ack();self.assertEqual(self.box.poll()['items'][0]['status'],'awaiting_manager_ack')
        self.ack('reply2');self.assertEqual(self.box.poll()['items'],[])

    def test_escalation_ack_retains_unresolved_owner_question(self):
        bid=self.ready();self.prepare(bid,'escalate')
        self.append(self.echoed(bid));self.ack()
        self.assertEqual(self.box.poll()['items'][0]['status'],'waiting_owner')
        self.append(self.entry('decision',routing=False,reply_to='m1',text='[decision job-1] Owner chose the documented option'))
        self.assertEqual(self.box.poll()['items'],[])

    def test_manager_resolution_before_reply_prevents_new_action(self):
        bid=self.ready();token=self.box.claim(bid)['claim']
        self.append(self.entry('decision',routing=False,reply_to='m1',text='[answer job-1] Already settled'))
        self.box.poll()
        with self.assertRaises(Blocked):self.box.prepare(bid,token,{'kind':'settled','answer':'x','source':'y'})

    def test_owner_tag_cannot_leak_into_routine_reply(self):
        bid=self.ready();token=self.box.claim(bid)['claim']
        with self.assertRaises(Blocked):self.box.prepare(bid,token,{'kind':'settled','answer':'Earlier `@operator` example','source':'approved issue'})

    def test_prepare_rechecks_new_manager_resolution(self):
        bid=self.ready();token=self.box.claim(bid)['claim']
        self.append(self.entry('decision',routing=False,reply_to='m1',text='[answer job-1] Already answered'))
        with self.assertRaises(Blocked):self.box.prepare(bid,token,{'kind':'settled','answer':'x','source':'y'})

    def test_send_rechecks_resolution_and_never_calls_transport(self):
        bid=self.ready();self.prepare(bid)
        self.append(self.entry('decision',routing=False,reply_to='m1',text='[answer job-1] Already answered'))
        with self.assertRaises(Blocked):self.box.send(bid,lambda argv:self.fail('must not send'))

    def test_routing_identity_change_fails_closed(self):
        self.box.cfg={**self.cfg,'assistant':'different-assistant'}
        with self.assertRaises(Blocked):self.box.poll()

    def test_quiet_unchanged_poll(self):
        self.box.poll()
        self.assertFalse(self.box.poll()['changed'])

    def test_new_pending_question_is_not_starved_by_waiting_owner(self):
        bid=self.ready();self.prepare(bid,'escalate')
        self.append(self.echoed(bid));self.ack();self.box.poll()
        self.append(self.entry('new',key='q2'))
        item=self.box.poll(limit=1)['items'][0]
        self.assertTrue(item['claimable'])

    def test_missing_main_log_is_unknown_not_empty(self):
        (self.logs/'alpha.jsonl').unlink()
        with self.assertRaises(Blocked):self.box.poll()

    def test_removed_request_log_preserves_pending_question(self):
        self.append(self.entry(channel='#a-job-1'))
        self.box.poll();(self.logs/'a-job-1.jsonl').unlink()
        result=self.box.poll()
        self.assertEqual(len(result['items']),1);self.assertTrue(result['problems'])

    def test_failed_live_manager_preflight_preserves_prepared_unsent_action(self):
        bid=self.ready();self.prepare(bid)
        with patch.object(self.box,'verify_manager',side_effect=Blocked('denied')):
            with self.assertRaises(Blocked):self.box.send(bid,lambda argv:self.fail('must not send'))
        self.assertEqual(self.box.poll()['items'][0]['status'],'prepared')


if __name__ == '__main__':unittest.main()
