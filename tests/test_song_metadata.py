import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fa_bot.qa import answer
from fa_bot.song_metadata import research, RC_SOURCE, RC_URL
from test_flow import Response


class WebResponse(Response):
    def raise_for_status(self):
        pass

    @property
    def content(self):
        return NS(read=AsyncMock(return_value=b'Use primary metadata sources.'))


class SongMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_search_configuration_sources_and_cost_accounting(self):
        result = {'usage': {'input_tokens': 1000, 'output_tokens': 200}, 'output': [
            {'type': 'web_search_call', 'action': {'type': 'search', 'sources': [
                {'url': 'https://label.example/release', 'title': 'Official credits'}]}},
            {'type': 'web_search_call', 'action': {'type': 'open_page'}},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Artist: Example; Title: Song; Tags: example'}]}]}
        session = NS(get=Mock(return_value=WebResponse({})), post=Mock(return_value=Response(result)))
        evidence, cost = await research(session, 'secret', 'Song metadata please')
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args, (RC_SOURCE,))
        payload = session.post.call_args.kwargs['json']
        self.assertEqual(payload['max_tool_calls'], 2)
        self.assertTrue(payload['tools'][0]['external_web_access'])
        self.assertEqual(payload['tools'][0]['type'], 'web_search')
        self.assertIn('Use primary metadata sources.', payload['input'][0]['content'])
        self.assertEqual(evidence[0]['url'], RC_URL)
        self.assertIn('https://label.example/release', evidence[0]['text'])
        self.assertAlmostEqual(cost, .010225)

    async def test_rc_failure_does_not_claim_verification_or_search(self):
        session = NS(get=Mock(side_effect=TimeoutError), post=Mock())
        evidence, cost = await research(session, 'secret', 'Song metadata')
        session.post.assert_not_called()
        self.assertEqual(cost, 0)
        self.assertIn('Do not claim RC verification', evidence[0]['text'])

    async def test_one_research_per_answer_no_proposal_and_followup_context(self):
        outputs = [
            [{'type': 'function_call', 'name': 'research_song_metadata', 'call_id': str(i), 'arguments': '{}'}]
            for i in range(2)]
        outputs.append([{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({
            'answer': 'Artist: Example; Title: Song; Tags: example', 'development_status': 'ready',
            'missing_information': [], 'change_request': 'Change metadata automatically'})}]}])
        calls = []
        def post(url, **kwargs):
            calls.append(kwargs['json'])
            return Response({'usage': {'input_tokens': 100, 'output_tokens': 50}, 'output': outputs.pop(0)})
        knowledge = NS(sha='abc', updated='now', search=Mock(), read_evidence=Mock())
        with patch('fa_bot.qa.research', AsyncMock(return_value=([
                {'source': 'Internet', 'url': RC_URL, 'text': 'Official credit evidence'}], .021))) as lookup:
            reply, cost = await answer(NS(post=post), 'key', knowledge, '这个版本的meta呢',
                                       context=[{'role': 'user', 'content': 'ray version'}])
        lookup.assert_awaited_once()
        self.assertIn('ray version', lookup.call_args.args[2])
        self.assertIsNone(reply.change_request)
        self.assertGreater(cost, .021)
        self.assertFalse(any(t['name'] == 'research_song_metadata' for t in calls[1]['tools']))
        knowledge.search.assert_not_called()
        knowledge.read_evidence.assert_not_called()
