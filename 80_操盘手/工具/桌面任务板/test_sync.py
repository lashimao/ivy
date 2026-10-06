import importlib.util, json, tempfile, unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('sync',Path(__file__).with_name('sync.py'))
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class ProgressTests(unittest.TestCase):
    def status(self,events):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'events.jsonl';p.write_text('\n'.join(json.dumps({'type':'event_msg','payload':e}) for e in events))
            return s.tail_status(p,{})
    def test_round_complete_is_not_task_done(self):
        r=self.status([{'type':'task_complete','turn_id':'one','last_agent_message':'部分完成，还缺材料。'}])
        self.assertEqual(r['state'],'review');self.assertEqual(r['revision'],'one')
    def test_new_turn_reopens_progress(self):
        r=self.status([{'type':'task_complete','turn_id':'one'},{'type':'task_started','turn_id':'two'}])
        self.assertEqual(r['state'],'running');self.assertEqual(r['revision'],'two')
    def test_tool_outputs_are_never_copied(self):
        r=self.status([{'type':'item_completed','item':{'type':'CommandExecution','output':'secret-string'}}])
        self.assertNotIn('secret-string',r['detail'])
    def test_new_agent_format(self):
        r=self.status([{'type':'item_completed','turn_id':'one','item':{'type':'AgentMessage','phase':'commentary','content':[{'type':'Text','text':'已编译，正在验收。'}]}}])
        self.assertEqual(r['state'],'running');self.assertEqual(r['detail'],'已编译，正在验收。')
    def test_missing_source_not_completed(self):
        self.assertEqual(s.tail_status('/nonexistent/board-source',{})['state'],'review')
if __name__=='__main__':unittest.main()
