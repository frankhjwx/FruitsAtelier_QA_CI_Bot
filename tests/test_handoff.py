import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from fa_bot.handoff import encode, decode, locator
from fa_bot.qa import answer, INSTRUCTIONS
from context_pack import query_terms, evidence_pack
from prepare_task import prepare
from test_flow import Session, Response
import worker


class LocalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_qa_evidence_reaches_ci_without_querying_raw_request(self):
        source = 'src/Test.cs (Selection.Measure)'
        class Knowledge:
            sha = 'a'*40
            updated = 'today'
            def search(self, query):
                self.query = query
                return [{'source':source, 'url':f'https://github.com/frankhjwx/FruitsAtelier/blob/{self.sha}/src/Test.cs#L3', 'text':'selection source'}]
            def read_evidence(self, path, start): return self.search('slider anchors')
        class QA(Session):
            def post(self, url, **kwargs):
                if not self.calls:
                    return super().post(url, **kwargs)
                if kwargs['json'].get('tool_choice') == {'type':'function','name':'read_evidence'}:
                    self.calls.append(kwargs['json'])
                    return Response({'usage':{'input_tokens':100,'output_tokens':30},'output':[{'type':'function_call','name':'read_evidence','call_id':'read','arguments':json.dumps({'path':'src/Test.cs','start':3})}]})
                self.calls.append(kwargs['json'])
                return Response({'usage':{'input_tokens':100,'output_tokens':30}, 'output':[
                    {'type':'message','content':[{'type':'output_text','text':json.dumps({
                        'answer':'Proposed change', 'change_request':'Show selected note time difference',
                        'code_sources':[source,'src/Invented.cs'],
                        'investigation_direction':'Inspect selection timestamps and add a two-note regression.'})}]}]})
        knowledge = Knowledge()
        result = await prepare(QA(), 'fake', knowledge, 'Please implement this feature in docs')
        request, info = decode(result['request'])
        self.assertTrue(result['accepted'])
        self.assertEqual(query_terms(result['request']), ['slider','anchors'])
        self.assertEqual(info['locations'][0]['path'], 'src/Test.cs')
        self.assertEqual(len(info['locations']), 1)
        self.assertIn('two-note regression', info['direction'])
        self.assertEqual(request, 'Show selected note time difference')
        self.assertGreater(result['qa_cost'], 0)

    async def test_no_proposal_remains_qa_only(self):
        class Knowledge:
            sha='abc'; updated='today'
            def search(self, query): return []
        result = await prepare(Session(), 'fake', Knowledge(), 'How do I use existing controls?')
        self.assertFalse(result['accepted'])
        self.assertIsNone(result['request'])
        self.assertIn('Existing support does not disprove a bug', INSTRUCTIONS)

    def test_locator_rejects_unseen_or_foreign_paths(self):
        evidence=[{'source':'bad','url':'https://example.com/src/Bad.cs#L1'}]
        self.assertEqual(locator('time','inspect',['bad','invented'],evidence,'sha')['locations'], [])
        original='A requirement '*300
        self.assertEqual(decode(encode(original, {'query':'time'}))[0], original)

    def test_specific_line_reference_must_be_in_observed_excerpt(self):
        sha='a'*40
        evidence=[{'source':'src/Preview.cs:1','url':f'https://github.com/frankhjwx/FruitsAtelier/blob/{sha}/src/Preview.cs#L1',
                   'text':'17: class Preview\n18: public bool Visible;'}]
        result=locator('preview','inspect',['src/Preview.cs:18'],evidence,sha)
        self.assertEqual(result['locations'][0]['line'],18)
        self.assertEqual(locator('preview','inspect',['src/Preview.cs:99'],evidence,sha)['locations'],[])
        self.assertEqual(locator('preview','inspect',['src/Unknown.cs:18'],evidence,sha)['locations'],[])

    def test_ci_reads_located_code_only_at_matching_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'src').mkdir()
            (root/'src/Target.cs').write_text('class Target { /* QA located implementation */ }')
            ws=worker.Workspace(root)
            for sha, expected in [('sha',True), ('older',False)]:
                request=encode('Add a small measurement', {'query':'time difference', 'commit':sha,
                    'locations':[{'path':'src/Target.cs','line':1}], 'direction':'Check selection'})
                with patch.object(worker.subprocess,'check_output',side_effect=['sha','src/Target.cs']):
                    ws.code_index=None
                    pack=evidence_pack(ws,request)
                self.assertEqual('QA LOCATED SOURCE src/Target.cs' in pack,expected)
                self.assertIn('QA LOCALIZATION',pack)


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_searches_keep_earlier_sources_and_stable_prefix(self):
        class Knowledge:
            sha = 'a'*40
            updated = 'today'
            def search(self, query, prefix=''):
                path = 'src/Selection.cs' if query == 'selection' else 'tests/SelectionTests.cs'
                return [{'source':path, 'url':f'https://github.com/frankhjwx/FruitsAtelier/blob/{self.sha}/{path}#L1', 'text':query}]
            def read_evidence(self, path, start): return self.search('selection')
        class Multi(Session):
            def post(self, url, **kwargs):
                self.calls.append(kwargs['json'])
                n = len(self.calls)
                if kwargs['json'].get('tool_choice') == {'type':'function','name':'read_evidence'}:
                    return Response({'usage':{'input_tokens':100,'output_tokens':30},'output':[{'type':'function_call','name':'read_evidence','call_id':'read','arguments':json.dumps({'path':'src/Selection.cs','start':1})}]})
                output = ([{'type':'function_call', 'name':'search_evidence', 'call_id':str(n),
                            'arguments':json.dumps({'query':'selection' if n == 1 else 'timestamp', 'prefix':'' if n == 1 else 'tests/'})}]
                          if n < 3 else [{'type':'message', 'content':[{'type':'output_text','text':json.dumps({
                              'answer':'Located', 'change_request':'Display selected timestamps',
                              'code_sources':['src/Selection.cs','tests/SelectionTests.cs'], 'investigation_direction':'Inspect selection and regression'})}]}])
                return Response({'output':output, 'usage':{'input_tokens':100,'output_tokens':30}})
        session=Multi()
        reply,cost=await answer(session,'fake',Knowledge(),'Display selected timestamps')
        self.assertEqual(len(reply.localization['locations']),2)
        self.assertEqual(len(session.calls),5)
        self.assertEqual(session.calls[2]['input'][:len(session.calls[1]['input'])],session.calls[1]['input'])
        self.assertEqual(session.calls[0]['tools'],session.calls[2]['tools'])

    async def test_document_only_proposal_is_not_dispatchable(self):
        from fa_bot.handoff import ready
        self.assertFalse(ready({'query':'selection', 'direction':'inspect', 'commit':'a'*40,
                                'locations':[{'path':'docs/TODO.md'}]}))
        from fa_bot.bot import Bot
        from fa_bot.qa import QAReply
        from unittest.mock import AsyncMock
        from types import SimpleNamespace as NS
        import os
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE':str(Path(folder)/'state.sqlite3')}):
            bot=Bot()
            try:
                bot.github=NS(dispatch=AsyncMock())
                bot.ask=AsyncMock(return_value=QAReply('Insufficient source evidence', 'Display selected timestamps', localization={'query':'selection'}))
                interaction=NS(id=12345678901234567,user=NS(id=1),channel_id=2,guild_id=1554882915945414726)
                result=await bot.start_job(interaction,'Display selected timestamps')
                self.assertIn('CI has not started',result)
                bot.github.dispatch.assert_not_awaited()
                self.assertIsNone(bot.store.latest_job())
            finally:
                bot.store.db.close()
