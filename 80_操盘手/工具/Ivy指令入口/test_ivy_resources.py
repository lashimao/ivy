import io
import json
import queue
import sqlite3
import unittest
from unittest.mock import Mock
import codex_rpc
import ivy_service as s


class ResourceTests(unittest.TestCase):
    def test_unused_large_stream_does_not_accumulate(self):
        rpc = object.__new__(codex_rpc.RPC)
        ignored = {'method': 'item/commandExecution/outputDelta', 'params': {'delta': 'x' * 10000}}
        tool = {'method': 'item/completed', 'params': {'item': {'type': 'commandExecution', 'output': 'x' * 10000}}}
        final = {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'done'}}}
        terminal = {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}
        rpc.process = Mock(stdout=iter([json.dumps(ignored)] * 1000 + [json.dumps(tool), json.dumps(final), json.dumps(terminal)]))
        rpc.events = queue.Queue(); rpc.pending = {}
        rpc.read()
        self.assertEqual(rpc.events.qsize(), 3)
        self.assertEqual(rpc.events.get(), final)
        self.assertEqual(rpc.events.get(), terminal)

    def test_capacity_keeps_input_durable_then_dispatches(self):
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,created_ms INTEGER,kind TEXT,content TEXT,status TEXT,note TEXT,updated_ms INTEGER)')
        s.setup(db)
        for n in range(s.MAX_ACTIVE_TASKS):
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,1)', (str(n),str(n),'busy'+str(n),'t','running'))
        db.execute("INSERT INTO tasks VALUES('next','Next','thread',NULL,'idle',1)")
        db.execute("INSERT INTO messages VALUES('m',1,'text','{}','queued','',1)")
        db.execute("INSERT INTO deliveries VALUES('m','next','queued')")
        service = s.Service(db, Mock()); service.loaded.add('thread'); service.turn = Mock(return_value='new')
        service.dispatch('next'); service.turn.assert_not_called()
        self.assertEqual(db.execute("SELECT status FROM deliveries WHERE message_id='m'").fetchone()[0], 'queued')
        db.execute("UPDATE tasks SET status='done' WHERE key='0'")
        service.dispatch('next'); service.turn.assert_called_once()

    def test_release_does_not_archive_or_delete(self):
        rpc = Mock(); service = s.Service(None, rpc); service.loaded.add('thread')
        service.release('thread')
        rpc.call.assert_called_once_with('thread/unsubscribe', {'threadId':'thread'})
        self.assertNotIn('thread', service.loaded)

if __name__ == '__main__':
    unittest.main()
