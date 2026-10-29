import tempfile
import unittest
from unittest.mock import patch
import worker


class UsageBudgetTests(unittest.TestCase):
    def test_measured_cache_reads_and_writes_are_not_double_counted(self):
        usage={'input_tokens':12000,'output_tokens':100,
               'input_tokens_details':{'cached_tokens':4000,'cache_write_tokens':7000,
                 'cache_write_tokens_details':{'ttl_30m_tokens':3000,'ttl_12h_tokens':4000}}}
        self.assertAlmostEqual(worker.usage_cost(usage,.1),.0309)
        usage['input_tokens_details']['cached_tokens']=13000
        self.assertAlmostEqual(worker.usage_cost(usage,.1),.031)

    def test_can_finish_after_twelve_queries_within_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            ws = worker.Workspace(folder)
            responses = [{'usage':{'input_tokens':100,'output_tokens':10},
                          'output':[{'type':'function_call','name':'search','call_id':str(i),
                                     'arguments':'{"query":"anchor"}'}]} for i in range(12)]
            responses.append({'usage':{'input_tokens':100,'output_tokens':10},'status':'completed',
                              'output':[{'type':'message','content':[{'type':'output_text','text':'Finished.'}]}]})
            with patch.object(worker,'request_json',side_effect=responses) as request, patch.object(ws,'search',return_value='Focused result'):
                summary, cost = worker.generate(ws,'Inspect anchor behavior','fake')
            self.assertEqual(summary,'Finished.')
            self.assertEqual(request.call_count,13)
            self.assertIn('38 model turns remain', request.call_args.args[3]['input'][-1]['content'][0]['text'])
            self.assertEqual(request.call_args_list[0].args[3]['instructions'],request.call_args.args[3]['instructions'])
            first=request.call_args_list[0].args[3]['input']
            second=request.call_args_list[1].args[3]['input']
            self.assertEqual(first,second[:len(first)])
            self.assertEqual(first[-1]['content'][0]['prompt_cache_breakpoint'],{'mode':'explicit'})
            self.assertEqual(request.call_args.args[3]['reasoning']['context'],'all_turns')
            self.assertLess(cost,worker.CAP)

    def test_large_read_batch_is_compacted_before_next_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            ws = worker.Workspace(folder)
            calls = [{'type':'function_call','name':'read','call_id':str(i),
                      'arguments':'{"path":"src/Test.cs","start":1}'} for i in range(8)]
            responses = [
                {'usage':{'input_tokens':100,'output_tokens':100},'output':calls},
                {'usage':{'input_tokens':10000,'output_tokens':50},'status':'completed',
                 'output':[{'type':'message','content':[{'type':'output_text','text':'Finished.'}]}]}]
            with patch.object(worker,'request_json',side_effect=responses) as request, patch.object(ws,'read',return_value='x'*14000):
                summary, cost = worker.generate(ws,'Inspect the anchor behavior','fake')
            self.assertEqual(summary,'Finished.')
            self.assertEqual(request.call_count,2)
            history = request.call_args.args[3]['input']
            outputs = [x for x in history if x.get('type')=='function_call_output']
            self.assertEqual(len(outputs),8)
            self.assertIn('omitted',outputs[0]['output'][0]['text'])
            self.assertEqual(outputs[-1]['output'][0]['text'],'x'*14000)
            self.assertLess(cost,worker.CAP)

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

    def test_retry_cannot_reset_the_task_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(worker,'request_json') as request:
                with self.assertRaisesRegex(RuntimeError,'budget reached'):
                    worker.generate(worker.Workspace(folder),'Continue the repair','fake',used=.499)
                request.assert_not_called()

    def test_missing_usage_keeps_full_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            ws=worker.Workspace(folder)
            response={'output':[{'type':'function_call','name':'search','call_id':'1','arguments':'{"query":"theme"}'}]}
            with patch.object(worker,'CAP',.065), patch.object(worker,'request_json',return_value=response) as request, patch.object(ws,'search',return_value='Theme information'):
                with self.assertRaisesRegex(RuntimeError,'budget reached'):
                    worker.generate(ws,'Inspect theme behavior','fake')
            self.assertEqual(request.call_count,1)


    def test_large_tool_batch_returns_results_and_defers_excess(self):
        with tempfile.TemporaryDirectory() as folder:
            ws=worker.Workspace(folder)
            calls=[{'type':'function_call','name':'search','call_id':str(i),
                    'arguments':'{"query":"theme"}'} for i in range(13)]
            responses=[{'usage':{'input_tokens':100,'output_tokens':100},'output':calls},
                       {'usage':{'input_tokens':400,'output_tokens':50},'status':'completed',
                        'output':[{'type':'message','content':[{'type':'output_text','text':'Finished.'}]}]}]
            with patch.object(worker,'request_json',side_effect=responses) as request, patch.object(ws,'search',return_value='Result') as search:
                summary,cost=worker.generate(ws,'Inspect themes','fake')
            self.assertEqual(summary,'Finished.')
            self.assertEqual(search.call_count,12)
            history=request.call_args.args[3]['input']
            outputs=[x for x in history if x.get('type')=='function_call_output']
            self.assertEqual(len(outputs),13)
            self.assertIn('retry this operation',outputs[-1]['output'][0]['text'])
