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
        class QA(Session):
            def post(self, url, **kwargs):
                if not self.calls:
                    return super().post(url, **kwargs)
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
        self.assertEqual(knowledge.query, 'slider anchors')
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
