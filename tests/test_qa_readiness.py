import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from fa_bot.qa import answer
from fa_bot.messages import qa_message
from test_flow import Session, Response


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_facts_or_unclear_intent_suppress_even_a_drafted_change(self):
        for status,missing in (('clarify',['import method']),('ready',['observed import result']),
                               ('none',[]),(None,None)):
            with self.subTest(status=status,missing=missing):
                class QA(Session):
                    def post(self,url,**kwargs):
                        if not self.calls: return super().post(url,**kwargs)
                        self.calls.append(kwargs['json'])
                        data={'answer_language':'en','answer':'Which import method did you use, and what happened next?',
                              'change_request':'Fix OSZ import visibility in the library.',
                              'code_sources':['src/Import.cs'],'investigation_direction':'Inspect the import handler.'}
                        if status is not None:
                            data.update(development_status=status,missing_information=missing)
                        return Response({'usage':{'input_tokens':100,'output_tokens':50},'output':[
                            {'type':'message','content':[{'type':'output_text','text':json.dumps(data)}]}]})
                knowledge=NS(sha='a'*40,updated='test',read_evidence=Mock(),search=Mock(return_value=[{
                    'source':'src/Import.cs','url':'https://github.com/frankhjwx/FruitsAtelier/blob/'+'a'*40+'/src/Import.cs#L1',
                    'text':'public void ImportOsz() {}'}]))
                session=QA()
                reply,_=await answer(session,'test',knowledge,"Imported oszs don't seem to appear anywhere")
                self.assertIsNone(reply.change_request)
                self.assertIsNone(reply.localization)
                self.assertIn('Which import method',reply.text)
                self.assertIsNone(qa_message(reply,NS(),1,2)['view'])
                knowledge.read_evidence.assert_not_called()
                self.assertEqual(len(session.calls),2)
