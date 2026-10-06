import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import grok_channel as g


def terminal(model='grok-4.6-build', text=None):
    return {'event_types':['text','end'], 'tool_calls':0,
            'end':{'stopReason':'end_turn','modelUsage':{model:{}}},
            'text': text or json.dumps({'status':'done','reply':'验收完成','note':'verified','deliver_text_files':[]})}


class GrokTests(unittest.TestCase):
    def test_actual_model_and_terminal_required(self):
        self.assertEqual(g.validate(terminal(),0)['status'],'done')
        for state, rc in [(terminal('other'),0), ({'text':'ok'},0), (terminal(),1),
                          ({**terminal(),'error':'failed'},0)]:
            with self.assertRaises(ValueError):g.validate(state,rc)

    def test_probe_and_bad_deliverable(self):
        self.assertEqual(g.validate(terminal(text='IVY_GROK_CHANNEL_OK'),0,True),{'probe':'passed'})
        with self.assertRaises(ValueError):g.validate(terminal(text='wrong'),0,True)
        body={'status':'done','reply':'ok','note':'ok','deliver_text_files':['/etc/passwd']}
        with self.assertRaises(ValueError):g.validate(terminal(text=json.dumps(body)),0)

    def test_progress_before_final_json(self):
        state=terminal()
        state['text']='正在读取。正在核验。'+state['text']
        self.assertEqual(g.validate(state,0)['status'],'done')
        state['text']+=' 此后失败'
        with self.assertRaises(ValueError):g.validate(state,0)

    def test_session_resume_and_idempotency(self):
        with tempfile.TemporaryDirectory() as td, patch.object(g,'ROOT',Path(td)), \
             patch.object(g.CLI.__class__,'is_file',return_value=True), \
             patch.object(g,'execute',new_callable=AsyncMock) as execute:
            execute.return_value=(terminal(),0)
            task=Path(td)/'任务/example'
            first=g.invoke('key',task,'request-1','task one')
            again=g.invoke('key',task,'request-1','task one')
            self.assertEqual(first,again);self.assertEqual(execute.call_count,1)
            with self.assertRaises(ValueError):g.invoke('key',task,'request-1','changed')
            second=g.invoke('key',task,'request-2','followup')
            self.assertEqual(first['session_id'],second['session_id'])
            self.assertTrue(second['resumed'])
            args=execute.call_args.args[0]
            self.assertIn('--resume',args);self.assertIn('grok-4.6',args);self.assertIn('xhigh',args)
            with self.assertRaises(ValueError):g.invoke('key',Path(td)/'任务/other','request-3','same key')
            other=g.invoke('other',Path(td)/'任务/other','request-1','another')
            self.assertNotEqual(first['session_id'],other['session_id'])

    def test_uncertain_never_replays_or_falls_back(self):
        with tempfile.TemporaryDirectory() as td, patch.object(g,'ROOT',Path(td)), \
             patch.object(g.CLI.__class__,'is_file',return_value=True), \
             patch.object(g,'execute',new_callable=AsyncMock) as execute:
            execute.return_value=({'event_types':[],'tool_calls':0,'text':''},1)
            task=Path(td)/'任务/example'
            result=g.invoke('key',task,'one','task')
            self.assertEqual(result['status'],'uncertain')
            self.assertEqual(g.invoke('key',task,'one','task'),result)
            with self.assertRaises(ValueError):g.invoke('key',task,'two','task')
            self.assertEqual(execute.call_count,1)

    def test_readback_recovers_parser_failure_without_execution(self):
        with tempfile.TemporaryDirectory() as td, patch.object(g,'ROOT',Path(td)), \
             patch.object(g.CLI.__class__,'is_file',return_value=True), \
             patch.object(g,'execute',new_callable=AsyncMock) as execute:
            execute.return_value=(terminal(),0)
            task=Path(td)/'任务/example'
            with patch.object(g,'validate',side_effect=ValueError('old parser')):
                result=g.invoke('key',task,'one','task')
            self.assertEqual(result['status'],'uncertain')
            result=g.readback('key',task,'one')
            self.assertEqual(result['status'],'verified')
            self.assertEqual(execute.call_count,1)


if __name__=='__main__':unittest.main()
