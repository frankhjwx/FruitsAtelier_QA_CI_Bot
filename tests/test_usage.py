import tempfile
import unittest
from unittest.mock import patch
import worker


class UsageBudgetTests(unittest.TestCase):
    def test_measured_usage_releases_unused_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            ws=worker.Workspace(folder)
            replies=[{'usage':{'input_tokens':100,'output_tokens':100},
                      'output':[{'type':'function_call','name':'search','call_id':'1','arguments':'{"query":"theme"}'}]},
                     {'usage':{'input_tokens':300,'output_tokens':50},'status':'completed',
                      'output':[{'type':'message','content':[{'type':'output_text','text':'No change needed.'}]}]}]
            with patch.object(worker,'CAP',.065), patch.object(worker,'request_json',side_effect=replies) as request, patch.object(ws,'search',return_value='Theme information'):
                summary,cost=worker.generate(ws,'Inspect theme behavior','fake')
            self.assertEqual(request.call_count,2)
            self.assertAlmostEqual(cost,.0025)

    def test_missing_usage_keeps_full_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            ws=worker.Workspace(folder)
            response={'output':[{'type':'function_call','name':'search','call_id':'1','arguments':'{"query":"theme"}'}]}
            with patch.object(worker,'CAP',.065), patch.object(worker,'request_json',return_value=response) as request, patch.object(ws,'search',return_value='Theme information'):
                with self.assertRaisesRegex(RuntimeError,'budget reached'):
                    worker.generate(ws,'Inspect theme behavior','fake')
            self.assertEqual(request.call_count,1)
