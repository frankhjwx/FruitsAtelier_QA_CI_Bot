import tempfile
import unittest
from unittest.mock import patch
import worker
from repair_ci import RepairWorkspace


class CompletionTests(unittest.TestCase):
    def test_early_text_is_saved_and_explicit_blocker_ends_without_resetting_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            ws=RepairWorkspace(directory,directory,{'files':[]})
            replies=[{'status':'completed','usage':{'input_tokens':100,'output_tokens':20},
                      'output':[{'type':'message','content':[{'type':'output_text','text':'I can do this later.'}]}]},
                     {'status':'completed','usage':{'input_tokens':100,'output_tokens':20},
                      'output':[{'type':'function_call','name':'finish','call_id':'f',
                                 'arguments':'{"status":"blocked","summary":"Required capability unavailable"}'}]}]
            tools=[worker.tool('finish',{'status':{'type':'string'},'summary':{'type':'string'}})]
            with patch.object(worker,'request_json',side_effect=replies) as request:
                summary,cost=worker.generate(ws,'Implement one behavior','fake',used=.1,extra_tools=tools)
            self.assertEqual(request.call_count,2)
            self.assertEqual(ws.finish_result['status'],'blocked')
            self.assertEqual(ws.diagnostics[0]['final_text'],'I can do this later.')
            self.assertAlmostEqual(cost,.1009)

    def test_ready_requires_both_edits_and_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            ws=RepairWorkspace(directory,directory,{'files':[]})
            with self.assertRaisesRegex(ValueError,'Ready requires'):
                ws.finish('ready','Finished')
            ws.changed.add('src/Test.cs')
            with self.assertRaises(ValueError): ws.finish('ready','Finished')
            ws.set_regression('core','test')
            ws.finish('ready','Changed behavior; tests pending')
            self.assertEqual(ws.finish_result['status'],'ready')
