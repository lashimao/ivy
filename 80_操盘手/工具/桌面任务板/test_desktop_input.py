import importlib.util,json,sqlite3,tempfile,unittest,uuid
from pathlib import Path
spec=importlib.util.spec_from_file_location('desktop_input',Path(__file__).with_name('desktop_input.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class InputTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'inbox.sqlite3';c=sqlite3.connect(self.db)
  c.executescript('CREATE TABLE messages(id TEXT PRIMARY KEY,created_ms INTEGER,kind TEXT,content TEXT,status TEXT,note TEXT,updated_ms INTEGER);CREATE TABLE tasks(key TEXT PRIMARY KEY,title TEXT,thread_id TEXT UNIQUE,turn_id TEXT,status TEXT,updated INTEGER);CREATE TABLE deliveries(message_id TEXT PRIMARY KEY,task_key TEXT,status TEXT);CREATE TABLE task_channels(task_key TEXT PRIMARY KEY,channel TEXT);');c.close()
 def tearDown(self):self.tmp.cleanup()
 def req(self,**kw):return dict(id=str(uuid.uuid4()),text='读取进展',channel='auto',target_id='',**kw)
 def submit(self,r,tasks=[]):return m.submit(r,self.db,{'tasks':tasks},wake=False)
 def test_exactly_once(self):
  r=self.req();self.assertFalse(self.submit(r)['duplicate']);self.assertTrue(self.submit(r)['duplicate']);c=sqlite3.connect(self.db);self.assertEqual(c.execute('select count(*) from messages').fetchone()[0],1)
 def test_id_cannot_change_content(self):
  r=self.req();self.submit(r);r['text']='不同指令'
  with self.assertRaises(ValueError):self.submit(r)
 def test_stale_target_rejected(self):
  r=self.req();r['target_id']='missing'
  with self.assertRaises(ValueError):self.submit(r)
 def test_direct_codex_reuses_thread_and_waits_for_idle(self):
  t=dict(id='codex:abc',source='Codex',title='原名',thread='abc',revision='turn1');r=self.req();r['target_id']=t['id'];self.submit(r,[t]);c=sqlite3.connect(self.db);self.assertEqual(c.execute('select title,thread_id,status from tasks').fetchone(),('原名','abc','uncertain'));self.assertEqual(c.execute('select status from messages').fetchone()[0],'queued')
 def test_claude_continues_exact_session(self):
  t=dict(id='claude:local_abc',source='Claude',title='Claude原名',thread='local_abc',url='https://claude.ai/epitaxy/local_abc');r=self.req();r['target_id']=t['id'];self.submit(r,[t]);c=sqlite3.connect(self.db);text=json.loads(c.execute('select content from messages').fetchone()[0])['text'];self.assertTrue(text.startswith('[OK]'));self.assertIn('local_abc',text);self.assertIn('不新建替代会话',text)
 def test_empty_input_does_not_write(self):
  r=self.req();r['text']=' '
  with self.assertRaises(ValueError):self.submit(r)
  c=sqlite3.connect(self.db);self.assertEqual(c.execute('select count(*) from messages').fetchone()[0],0)
 def test_grok_global_marker_and_duplicate(self):
  r=self.req();r['channel']='grok_cli';self.submit(r);self.assertTrue(self.submit(r)['duplicate'])
  c=sqlite3.connect(self.db);self.assertTrue(json.loads(c.execute('select content from messages').fetchone()[0])['text'].startswith('[看]'))
 def test_grok_resume_keeps_binding(self):
  c=sqlite3.connect(self.db);c.execute("insert into tasks values('grok_task','原名','worker','turn','done',0)");c.execute("insert into task_channels values('grok_task','grok_cli')");c.commit()
  t=dict(id='ivy:grok_task',source='Grok',title='原名',thread='worker');r=self.req();r['target_id']=t['id'];self.submit(r,[t])
  self.assertEqual(c.execute('select task_key from deliveries').fetchone()[0],'grok_task');self.assertEqual(c.execute('select channel from task_channels').fetchone()[0],'grok_cli')
 def test_ivy_claude_worker_is_not_app_session(self):
  c=sqlite3.connect(self.db);c.execute("insert into tasks values('claude_task','原名','worker','turn','done',0)");c.commit()
  t=dict(id='ivy:claude_task',source='Claude',title='原名',thread='worker');r=self.req();r['target_id']=t['id'];self.submit(r,[t])
  prompt=json.loads(c.execute('select content from messages').fetchone()[0])['text'];self.assertNotIn('session_id',prompt)
if __name__=='__main__':unittest.main()

class ProjectInputTests(unittest.TestCase):
 setUp=InputTests.setUp
 tearDown=InputTests.tearDown
 req=InputTests.req
 submit=InputTests.submit
 def test_project_context_is_bound_and_not_broad_authorization(self):
  r=self.req();r['project_id']='project:p'
  board={'tasks':[],'projects':[{'id':'project:p','title':'产品','goal':'发布'}]}
  m.submit(r,self.db,board,wake=False)
  c=sqlite3.connect(self.db);content=json.loads(c.execute('select content from messages').fetchone()[0])
  self.assertEqual(content['desktop_context']['project']['id'],'project:p')
  self.assertIn('不授权',content['desktop_context']['scope']);self.assertEqual(c.execute('select project_id from desktop_inputs').fetchone()[0],'project:p')
 def test_request_cannot_change_project_on_retry(self):
  r=self.req();b={'tasks':[],'projects':[{'id':'project:p','title':'产品','goal':''}]};m.submit(r,self.db,b,wake=False);r['project_id']='project:p'
  with self.assertRaises(ValueError):m.submit(r,self.db,b,wake=False)
 def test_deleted_project_rejected(self):
  r=self.req();r['project_id']='project:missing'
  with self.assertRaises(ValueError):self.submit(r)
