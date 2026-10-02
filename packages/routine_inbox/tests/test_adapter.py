"""Optional offline adapter integration. Supply CHAT_ADAPTER_UNDER_TEST.

Every state path is temporary and IRC/cmux are replaced with fakes. Installed
source is supplied by the test runner, never copied into this public package.
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from routine_inbox.protocol import tags


@unittest.skipUnless(os.environ.get('CHAT_ADAPTER_UNDER_TEST'),'no staged legacy adapter supplied')
class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        os.environ['CHAT_STATE']=cls.temp.name
        root=Path(os.environ['CHAT_ADAPTER_UNDER_TEST'])
        sys.path.insert(0,str(root))
        import chatlib
        cls.chatlib=chatlib
        loader=importlib.machinery.SourceFileLoader('staged_bridge',str(root/'chat-bridge'))
        spec=importlib.util.spec_from_loader('staged_bridge',loader)
        cls.bridge=importlib.util.module_from_spec(spec);loader.exec_module(cls.bridge)
        cls.cfg={'owner':'operator','assistants':['assistant'],
                 'accounts':dict.fromkeys(['operator','assistant','a-manager','a-worker'],'synthetic'),
                 'projects':{'alpha':{'channel':'#alpha','prefix':'#a-','manager':'a-manager','worker':'a-worker'}}}

    def setUp(self):
        for path in Path(self.temp.name).glob('**/*'):
            if path.is_file():path.unlink()
        self.r=self.bridge.Record();self.d=self.bridge.Deliveries();self.a=self.bridge.Acks()
        self.cmux=mock.patch.object(self.bridge,'_cmux',lambda *args:(0,'{}',''))
        self.cmux.start();self.addCleanup(self.cmux.stop)

    def handle(self,text,mid='msg',wire=None,sender='a-manager'):
        wire={'account':sender,'msgid':mid,'time':'2026-01-01T01:00:00Z',**(wire or {})}
        self.bridge.handle({'prefix':sender+'!u@host','params':['#alpha',text],'tags':wire},
                           self.cfg,self.r,self.d,self.a)

    def test_diagnostic_quote_does_not_push_but_explicit_address_does(self):
        self.handle('[question job-1] @a-manager Earlier @operator question was logged',sender='assistant')
        self.assertFalse(any(i['kind']=='push' for i in self.d.items))
        self.handle('[question job-1] owner decision','real',tags(['operator'],'key',1,1))
        self.assertEqual(sum(i['kind']=='push' for i in self.d.items),1)

    def test_structured_multipart_owner_message_pushes_once(self):
        self.handle('[question job-1] part one','one',tags(['operator'],'key',1,2))
        self.handle('… [question job-1] part two','two',tags(['operator'],'key',2,2))
        self.assertEqual(sum(i['kind']=='push' for i in self.d.items),1)

    def test_helper_is_destination_without_new_account(self):
        self.handle('[question job-1] default?','q',tags(['helper'],'key',1,1))
        self.assertEqual(self.d.items,[])
        rows=[json.loads(l) for l in self.chatlib.channel_log('#alpha').read_text().splitlines()]
        self.assertEqual(rows[0]['routing']['to'],['helper'])
        self.assertTrue(rows[0]['verified'])

    def test_malformed_envelope_never_falls_back_to_owner_tag(self):
        self.handle('@operator incidental','q',{'+plateia/to':'helper'})
        self.assertEqual(self.d.items,[])

    def test_post_emits_same_key_on_every_fragment_without_real_irc(self):
        class Fake:
            def close(self,reason):pass
        sends=[]
        with mock.patch.object(self.chatlib,'login',return_value=Fake()),mock.patch.object(
                self.chatlib,'_round',side_effect=lambda conn,lines:sends.extend(lines) or set()):
            count=self.chatlib.post('#alpha','[question job-1] '+('words '*150),'a-manager',self.cfg,
                                    cont='[question job-1]',recipients=['helper'],message_key='stable')
        self.assertGreater(count,1)
        decoded=[self.chatlib.parse_line(x)['tags'] for x in sends]
        self.assertEqual({d['+plateia/key'] for d in decoded},{'stable'})
        self.assertEqual([int(d['+plateia/part']) for d in decoded],list(range(1,count+1)))

    def test_outbox_preserves_recipient_and_key(self):
        self.chatlib.outbox_append([{'channel':'#alpha','as':'assistant','text':'answer','at':'t',
                                    'to':['a-manager'],'message_key':'stable','reply_to':'question'}])
        calls=[]
        self.bridge.flush_outbox(self.cfg,post=lambda *a,**kw:calls.append(kw))
        self.assertEqual(calls[0]['message_key'],'stable')
        self.assertEqual(calls[0]['recipients'],['a-manager'])
        self.assertEqual(calls[0]['reply_to'],'question')
