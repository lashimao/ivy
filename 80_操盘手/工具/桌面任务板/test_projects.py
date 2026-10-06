import copy,unittest
from project_model import apply,project_rows,EMPTY
class Projects(unittest.TestCase):
 def setUp(self):
  self.store,self.pid=apply(EMPTY,{'action':'create','title':'产品发布','goal':'交付可运行产品'},set())
  self.tasks=[{'id':'ivy:a','state':'done','updated':10},{'id':'codex:b','state':'running','updated':20}]
 def change(self,**kw):self.store,_=apply(self.store,dict(project_id=self.pid,**kw),{t['id'] for t in self.tasks})
 def test_sessions_do_not_complete_goal(self):
  self.change(action='assign',task_id='ivy:a');p=project_rows(self.tasks,self.store)[0]
  self.assertEqual(p['state'],'active');self.assertEqual(p['milestone_count'],0);self.assertEqual(p['completed'],0)
 def test_milestone_rollup(self):
  self.change(action='milestone_add',title='验收');mid=self.store['projects'][0]['milestones'][0]['id'];self.change(action='milestone_toggle',milestone_id=mid,done=True)
  self.assertEqual(project_rows(self.tasks,self.store)[0]['completed'],1)
 def test_assignment_preserves_identity(self):
  self.change(action='assign',task_id='codex:b');project_rows(self.tasks,self.store)
  self.assertEqual(self.tasks[1]['id'],'codex:b');self.assertEqual(self.tasks[1]['project_id'],self.pid)
 def test_move_and_unassign(self):
  self.change(action='assign',task_id='ivy:a');self.store,_=apply(self.store,{'action':'assign','task_id':'ivy:a','project_id':''},{'ivy:a'})
  self.assertEqual(project_rows(self.tasks,self.store)[0]['total'],0)
 def test_pause_survives_running_session(self):
  self.change(action='assign',task_id='codex:b');self.change(action='state',state='paused');p=project_rows(self.tasks,self.store)[0];self.assertEqual(p['label'],'已暂停')
 def test_invalid_edit_is_atomic(self):
  old=copy.deepcopy(self.store)
  with self.assertRaises(ValueError):self.change(action='edit',title='',goal='changed')
  self.assertEqual(self.store,old)
 def test_missing_task_rejected(self):
  with self.assertRaises(ValueError):self.change(action='assign',task_id='missing')
if __name__=='__main__':unittest.main()
