import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

import ivy_context as c
import ivy_service as s


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,created_ms INTEGER,kind TEXT,content TEXT,status TEXT,note TEXT,updated_ms INTEGER)')
        s.setup(self.db)
        self.rpc = Mock()
        self.service = s.Service(self.db, self.rpc)

    def tearDown(self):
        self.db.close()

    def message(self, mid, timestamp, text, key=None, status='done', note=''):
        self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',
                        (mid, timestamp, 'text', json.dumps({'text': text}, ensure_ascii=False), status, note, timestamp))
        if key:
            self.db.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,NULL,'done',?)", (key, key, key+'-thread', timestamp))
            self.db.execute('INSERT INTO deliveries VALUES(?,?,?)', (mid, key, status))

    def route(self, refs):
        return {'routes': [{'task_key': 'ranking', 'title': '排名', 'message_ids': ['new'],
                            'context_message_ids': refs, 'instruction': '根据前文写排名'}]}

    def prepare(self):
        self.message('old', 1, '核验家庭存款比例', 'savings', note='30万元不能据此推断前6%；报告 /task/report.html')
        self.message('new', 10, '写这个排名', status='pending')
        self.service.start = Mock(return_value='router')
        self.service.turn = Mock(return_value='route-turn')
        with patch.object(s.inbox, 'collect', return_value={'new_messages': 1}):
            self.service.poll()

    def test_router_sees_answer_and_source_despite_newer_notice_and_noise(self):
        self.message('old', 1, '核验家庭存款比例', 'savings', note='30万元前6%尚未证实 /task/report.html')
        self.message('notice', 2, '报告已发', 'savings', status='ignored', note='只留存通知')
        for i in range(50):
            self.message('noise'+str(i), 3+i, '无关素材', 'noise', status='ignored')
        payload = c.router_payload(self.db, [])
        savings = next(t for t in payload['tasks'] if t['key'] == 'savings')
        self.assertIn('30万元前6%', json.dumps(savings, ensure_ascii=False))
        self.assertIn('/task/report.html', json.dumps(savings))
        self.assertEqual(len(payload['recent_context_only']), c.RECENT_LIMIT)

    def test_current_batch_cannot_evict_recent_context(self):
        self.message('old', 1, '前文', 'old')
        for i in range(50):
            self.message('new'+str(i), 2+i, '本批', status='pending')
        rows = [dict(r) for r in self.db.execute("SELECT * FROM messages WHERE status='pending'")]
        payload = c.router_payload(self.db, rows)
        self.assertEqual([r['source_message_id'] for r in payload['recent_context_only']], ['old'])

    def test_end_to_end_context_survives_new_service_without_merging_tasks(self):
        self.prepare()
        self.service.route_result('router', json.dumps(self.route(['old'])))
        other = s.Service(self.db, self.rpc)
        other.start = Mock(return_value='worker')
        other.turn = Mock(return_value='worker-turn')
        other.dispatch('ranking')
        prompt = other.turn.call_args.args[1]
        self.assertIn('30万元不能据此推断前6%', prompt)
        self.assertIn('old', prompt)
        self.assertIn('/task/report.html', prompt)
        self.assertEqual(self.db.execute("SELECT status FROM tasks WHERE key='savings'").fetchone()[0], 'done')
        self.assertEqual(self.db.execute("SELECT task_key FROM deliveries WHERE message_id='old'").fetchone()[0], 'savings')
        saved = json.loads(self.db.execute('SELECT payload FROM dispatch_context').fetchone()[0])
        self.assertEqual(saved['related_context_only'][0]['source_message_id'], 'old')

    def test_invalid_context_rejects_entire_batch_before_mutation(self):
        self.prepare()
        for refs in [['unknown'], ['new'], ['old', 'old'], ['old'] * 13, [123], 'old']:
            with self.subTest(refs=refs), self.assertRaises(RuntimeError):
                self.service.route_result('router', json.dumps(self.route(refs)))
        self.assertIsNone(self.db.execute("SELECT * FROM deliveries WHERE message_id='new'").fetchone())

    def test_only_selected_related_material_reaches_worker(self):
        self.prepare()
        self.message('unrelated', 2, '别的隐私任务', 'private')
        self.service.route_result('router', json.dumps(self.route(['old'])))
        rows = [dict(r) for r in self.db.execute("SELECT * FROM messages WHERE id='new'")]
        payload = c.worker_payload(self.db, 'ranking', rows)
        self.assertNotIn('别的隐私任务', json.dumps(payload, ensure_ascii=False))

    def test_same_task_resume_gets_original_and_previous_result(self):
        self.message('first', 1, '原始要求', 'a', note='第一轮结果')
        for i in range(12):
            self.message('old'+str(i), i+2, '补充', 'a', note='后续结果')
        self.message('new', 20, '继续', 'a', status='queued')
        rows = [dict(r) for r in self.db.execute("SELECT * FROM messages WHERE id='new'")]
        payload = c.worker_payload(self.db, 'a', rows)
        ids = [r['source_message_id'] for r in payload['same_task_history']]
        self.assertIn('first', ids)
        self.assertIn('old11', ids)
        self.assertNotIn('new', ids)
        self.assertLessEqual(len(ids), c.HISTORY_LIMIT+1)

    def test_http_failure_does_not_block_durable_websocket_input(self):
        self.message('new', 1, '已由长连接收取', status='pending')
        self.service.start = Mock(return_value='router')
        self.service.turn = Mock(return_value='turn')
        with patch.object(s.inbox, 'collect', side_effect=OSError('offline')):
            self.service.poll()
        self.service.turn.assert_called_once()
        self.assertEqual(self.db.execute("SELECT status FROM messages WHERE id='new'").fetchone()[0], 'routing')

    def test_empty_http_failure_does_not_wake_model(self):
        with patch.object(s.inbox, 'collect', side_effect=OSError('offline')):
            self.service.poll()
        self.rpc.call.assert_not_called()

    def test_historical_marker_never_selects_execution_channel(self):
        self.prepare()
        self.db.execute("UPDATE messages SET content=? WHERE id='old'", (json.dumps({'text': '[OK] 旧素材'}),))
        self.service.route_result('router', json.dumps(self.route(['old'])))
        self.service.start = Mock(return_value='worker')
        self.service.turn = Mock(return_value='turn')
        self.service.dispatch('ranking')
        self.assertIn('"execution_channel": "codex"', self.service.turn.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
