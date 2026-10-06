import json
import sqlite3
import unittest
from unittest.mock import Mock,patch
import ivy_service as s

class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript("CREATE TABLE messages(id TEXT PRIMARY KEY,created_ms INTEGER,kind TEXT,content TEXT,status TEXT,note TEXT,updated_ms INTEGER);")
        s.setup(self.db);self.rpc=Mock();self.service=s.Service(self.db,self.rpc)
    def batch(self):
        self.db.executemany("INSERT INTO messages VALUES(?,1,'text','{}','routing','',1)",[('m1',),('m2',)])
        self.db.execute('INSERT INTO batches VALUES(?,?,?,?)',('router','turn',json.dumps(['m1','m2']),'running'));self.db.commit()
    def test_empty_poll_calls_no_model(self):
        with patch.object(s.inbox,'collect',return_value={'new_messages':0}):self.service.poll()
        self.rpc.call.assert_not_called()
    def test_poll_never_sends_ack(self):
        self.db.execute("INSERT INTO messages VALUES('m1',1,'text','{}','pending','',1)")
        self.service.start=Mock(return_value='router')
        self.service.turn=Mock(return_value='turn')
        with patch.object(s.inbox,'collect',return_value={'new_messages':1}), patch.object(s,'send_reply') as send:
            self.service.poll()
        send.assert_not_called()
        self.service.turn.assert_called_once()
    def test_empty_agent_reply_stays_silent(self):
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','turn','running',1)")
        self.service.finals['thread']=json.dumps({'status':'done','reply':'','note':'verified','deliver_text_files':[]})
        with patch.object(s,'send_reply') as send, patch.object(s.Path,'write_text'), patch.object(s.Path,'mkdir'):
            self.service.event({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
        send.assert_not_called()
    def test_failed_turn_never_sends_template(self):
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','turn','running',1)")
        with patch.object(s,'send_reply') as send, patch.object(s.Path,'write_text'), patch.object(s.Path,'mkdir'):
            self.service.event({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'failed'}}})
        send.assert_not_called()
    def test_agent_reply_is_transported_unchanged(self):
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','turn','running',1)")
        self.service.finals['thread']=json.dumps({'status':'done','reply':'我在','note':'verified','deliver_text_files':[]})
        with patch.object(s,'send_reply') as send, patch.object(s.Path,'write_text'), patch.object(s.Path,'mkdir'):
            self.service.event({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
        send.assert_called_once_with(self.db,'turn:turn','我在')
    def test_desktop_reply_stays_bound_to_its_message(self):
        self.db.execute('CREATE TABLE desktop_inputs(id TEXT PRIMARY KEY,reply TEXT,result_turn_id TEXT)')
        self.db.executemany('INSERT INTO desktop_inputs VALUES(?,?,?)',[('desktop:old','old answer','oldturn'),('desktop:new','','')])
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','turn','running',1)")
        self.db.executemany('INSERT INTO deliveries VALUES(?,?,?)',[('desktop:old','a','done'),('desktop:new','a','running')])
        self.service.finals['thread']=json.dumps({'status':'done','reply':'new answer','note':'','deliver_text_files':[]})
        with patch.object(s,'send_reply'),patch.object(s.Path,'write_text'),patch.object(s.Path,'mkdir'):
            self.service.event({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
        self.assertEqual(tuple(self.db.execute("SELECT reply,result_turn_id FROM desktop_inputs WHERE id='desktop:old'").fetchone()),('old answer','oldturn'))
        self.assertEqual(tuple(self.db.execute("SELECT reply,result_turn_id FROM desktop_inputs WHERE id='desktop:new'").fetchone()),('new answer','turn'))
    def test_independent_routes_and_followup(self):
        self.batch();self.service.route_result('router',json.dumps({'routes':[
            {'task_key':'a','title':'A','message_ids':['m1'],'instruction':'one'},
            {'task_key':'b','title':'B','message_ids':['m2'],'instruction':'two'}]}))
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT message_id,task_key FROM deliveries ORDER BY message_id')],[('m1','a'),('m2','b')])
        self.assertEqual(self.service.ready,{'a','b'})
    def test_duplicate_or_missing_route_has_no_dispatch(self):
        self.batch()
        with self.assertRaises(RuntimeError):self.service.route_result('router',json.dumps({'routes':[
            {'task_key':'a','title':'A','message_ids':['m1','m1'],'instruction':'one'}]}))
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
    def test_oversized_reply_is_not_sent(self):
        with patch.object(s.inbox,'connect') as c:s.send_reply(self.db,'test','字'*(s.REPLY_MAX+1))
        c.assert_not_called()
    def test_status_reply_is_transported(self):
        answer='接单：回复稿写好了，桌面看'
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','turn','running',1)")
        self.service.finals['thread']=json.dumps({'status':'done','reply':answer,'note':'n','deliver_text_files':[]})
        with patch.object(s,'send_reply') as send, patch.object(s.Path,'write_text'), patch.object(s.Path,'mkdir'):
            self.service.event({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
        send.assert_called_once_with(self.db,'turn:turn',answer)
    def test_poll_requeues_stuck_task_without_new_message(self):
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','old','uncertain',1)")
        self.db.execute("INSERT INTO deliveries VALUES('m1','a','queued')")
        with patch.object(s.inbox,'collect',return_value={'new_messages':0}):self.service.poll()
        self.assertEqual(self.service.ready,{'a'})
        self.rpc.call.assert_not_called()
    def test_uncertain_task_waits_while_turn_in_flight(self):
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','old','uncertain',1)")
        self.db.execute("INSERT INTO messages VALUES('m1',1,'text','{}','queued','',1)")
        self.db.execute("INSERT INTO deliveries VALUES('m1','a','queued')")
        self.rpc.call.return_value={'thread':{'turns':[{'id':'old','status':'inProgress'}]}}
        self.service.turn=Mock()
        self.service.dispatch('a');self.service.turn.assert_not_called()
        self.rpc.call.return_value={'thread':{'turns':[{'id':'old','status':'failed'}]}}
        self.service.loaded.add('thread');self.service.turn=Mock(return_value='new')
        self.service.dispatch('a');self.service.turn.assert_called_once()
    def test_deliverable_cannot_read_outside_task_dir(self):
        with self.assertRaises(RuntimeError):s.deliver_text_file(self.db,'bad','/private-example/.codex/auth.json')
    def test_receipt_not_resent(self):
        self.db.execute("INSERT INTO outbox VALUES('sent','好','sending',NULL)")
        with patch.object(s.inbox,'connect') as c:s.send_reply(self.db,'sent','好')
        c.assert_not_called()

    def test_direct_ok_marker_at_task_boundaries(self):
        for text in ['[OK] 写一份稿', '写一份稿[OK]', '  [OK] 写稿  ']:
            self.assertEqual(s.input_channel([{'kind':'text','content':json.dumps({'text':text})}]),'claude_app')

    def test_grok_boundaries_and_setup_exclusion(self):
        for text in ['[看] 写稿', '写稿[看]', '  [看] 写稿  ']:
            self.assertEqual(s.input_channel([{'kind':'text','content':json.dumps({'text':text})}]), 'grok_cli')
        for text in ['把grok 4.6 xhigh 把这个也加进来 默认使用grok cli，用这个表情触发[看]',
                     '以后用这个表情触发[OK]', '引用：[看] 写稿[看]', '以后用[看]作为标记']:
            self.assertIsNone(s.input_channel([{'kind':'text','content':json.dumps({'text':text})}]))
        self.assertIsNone(s.input_channel([{'kind':'image','content':json.dumps({'text':'写稿[看]'})}]))

    def test_grok_followup_and_explicit_switch(self):
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','old','idle',1)")
        self.db.execute("INSERT INTO task_channels VALUES('a','grok_cli')")
        self.db.execute("INSERT INTO messages VALUES('m1',1,'text',?,'queued','',1)",
                        (json.dumps({'text':'补充材料'}),))
        self.db.execute("INSERT INTO deliveries VALUES('m1','a','queued')")
        self.service.loaded.add('thread'); self.service.turn=Mock(return_value='new')
        self.service.dispatch('a')
        self.assertIn('"execution_channel": "grok_cli"', self.service.turn.call_args.args[1])
        self.assertIn('"dispatch_id":', self.service.turn.call_args.args[1])
        for text, channel in [('改回Codex 写稿','codex'), ('[OK] 写稿','claude_app'), ('[看] 写稿','grok_cli')]:
            self.db.execute("UPDATE tasks SET status='done' WHERE key='a'")
            self.db.execute("UPDATE deliveries SET status='queued' WHERE message_id='m1'")
            self.db.execute("UPDATE messages SET content=? WHERE id='m1'",(json.dumps({'text':text}),))
            self.service.dispatch('a')
            self.assertIn('"execution_channel": "'+channel+'"', self.service.turn.call_args.args[1])
            self.assertEqual(self.db.execute("SELECT channel FROM task_channels WHERE task_key='a'").fetchone()[0],channel)

    def test_marker_discussion_and_attachment_do_not_trigger(self):
        for row in [{'kind':'text','content':json.dumps({'text':'以后用[OK]作为标记'})},
                    {'kind':'image','content':json.dumps({'text':'[OK] 写稿'})},
                    {'kind':'text','content':json.dumps({'quoted_text':'[OK] 写稿'})},
                    {'kind':'text','content':json.dumps({'text':'引用：「[OK] 写稿」'})}]:
            self.assertIsNone(s.input_channel([row]))

    def test_marked_task_gets_app_channel_and_followup_retains_it(self):
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','old','idle',1)")
        self.db.execute("INSERT INTO messages VALUES('m1',1,'text',?,'queued','',1)",
            (json.dumps({'text':'[OK] 写稿'}),))
        self.db.execute("INSERT INTO deliveries VALUES('m1','a','queued')")
        self.service.loaded.add('thread');self.service.turn=Mock(return_value='new')
        self.service.dispatch('a')
        self.assertIn('"execution_channel": "claude_app"',self.service.turn.call_args.args[1])
        self.db.execute("UPDATE tasks SET status='done' WHERE key='a'")
        self.db.execute("UPDATE deliveries SET status='done' WHERE message_id='m1'")
        self.db.execute("INSERT INTO messages VALUES('m2',2,'text',?,'queued','',2)",
            (json.dumps({'text':'补充材料'}),))
        self.db.execute("INSERT INTO deliveries VALUES('m2','a','queued')")
        self.service.dispatch('a')
        self.assertIn('"execution_channel": "claude_app"',self.service.turn.call_args.args[1])

    def test_app_tasks_wait_for_other_app_task_but_codex_is_independent(self):
        self.db.execute("INSERT INTO tasks VALUES('busy','Busy','other','turn','running',1)")
        self.db.execute("INSERT INTO task_channels VALUES('busy','claude_app')")
        self.db.execute("INSERT INTO tasks VALUES('a','A','thread','old','idle',1)")
        self.db.execute("INSERT INTO messages VALUES('m1',1,'text',?,'queued','',1)",
            (json.dumps({'text':'[OK] 写稿'}),))
        self.db.execute("INSERT INTO deliveries VALUES('m1','a','queued')")
        self.service.turn=Mock(return_value='new');self.service.loaded.add('thread')
        self.service.dispatch('a');self.service.turn.assert_not_called()
        self.db.execute("UPDATE messages SET content=? WHERE id='m1'",(json.dumps({'text':'改回Codex 写稿'}),))
        # Channel exclusion is separate from the default single-worker resource cap.
        with patch.object(s,'MAX_ACTIVE_TASKS',2):
            self.service.dispatch('a')
        self.service.turn.assert_called_once()
        self.assertIn('"execution_channel": "codex"',self.service.turn.call_args.args[1])

if __name__=='__main__':unittest.main()
