import importlib.util
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ivy', Path(__file__).with_name('ivy_inbox.py'))
ivy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ivy)


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE messages(id TEXT PRIMARY KEY,created_ms INTEGER,kind TEXT,
          content TEXT,status TEXT DEFAULT 'pending',note TEXT DEFAULT '',updated_ms INTEGER);
        ''')
        for k,v in {'chat':'ivy','sender':'owner','started_ms':'100000','cursor_s':'100'}.items():
            ivy.save_meta(self.db,k,v)
        self.db.commit()

    def message(self, mid, sender='owner', created='101000'):
        return {'message_id':mid,'sender':{'id':sender,'sender_type':'user'},
                'create_time':created,'msg_type':'text','body':{'content':'{"text":"test"}'}}

    @patch.object(ivy, 'connect', return_value=('ivy','fake'))
    def test_filter_dedup_and_resume(self, _):
        data={'items':[self.message('a'),self.message('b','other'),self.message('old',created='99000')], 'has_more':False}
        with patch.object(ivy,'page',return_value=data):
            self.assertEqual(ivy.collect(self.db)['new_messages'],1)
            self.assertEqual(ivy.collect(self.db)['new_messages'],0)
        self.db.execute("UPDATE messages SET status='working',note='resume here'")
        self.assertEqual(ivy.pending(self.db)[0]['note'],'resume here')

    @patch.object(ivy, 'connect', return_value=('ivy','fake'))
    def test_pagination_failure_does_not_advance(self, _):
        first={'items':[self.message('a')],'has_more':True,'page_token':'next'}
        with patch.object(ivy,'page',side_effect=[first,RuntimeError('offline')]):
            with self.assertRaises(RuntimeError):
                ivy.collect(self.db)
        self.assertEqual(ivy.meta(self.db)['cursor_s'],'100')
        self.assertEqual(ivy.pending(self.db),[])

    @patch.object(ivy, 'connect', return_value=('ivy','fake'))
    def test_all_pages_persist(self, _):
        with patch.object(ivy,'page',side_effect=[
            {'items':[self.message('a')],'has_more':True,'page_token':'next'},
            {'items':[self.message('b')],'has_more':False}]):
            self.assertEqual(ivy.collect(self.db)['new_messages'],2)


if __name__ == '__main__':
    unittest.main()
