import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from fa_bot.bot import Bot
from fa_bot.qa import answer
from test_boundaries import worker


class Response:
    status = 200

    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.data


class Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(kwargs['json'])
        if len(self.calls) == 1:
            return Response({'output': [{'type': 'function_call', 'name': 'search_evidence',
                                         'call_id': 'call1', 'arguments': '{"query":"slider anchors"}'}],
                             'usage': {'input_tokens': 400, 'output_tokens': 30}})
        return Response({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Grounded answer'}]}],
                         'usage': {'input_tokens': 1000, 'output_tokens': 100}})


class Knowledge:
    sha = 'abc'
    updated = '2026-10-29'

    def search(self, query):
        return [{'url': 'https://example.com/evidence', 'text': 'Slider anchors'}]


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_qa_two_rounds_evidence_and_usage(self):
        session = Session()
        text, cost = await answer(session, 'fake', Knowledge(), 'How do anchors work?')
        self.assertEqual(text, 'Grounded answer')
        self.assertEqual(len(session.calls), 2)
        self.assertLess(cost, .02)
        self.assertIn('evidence', session.calls[1]['input'][-1]['output'])
        self.assertFalse(session.calls[0]['store'])

    async def test_revision_requires_owner_and_channel(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot = Bot()
            bot.store.add_job('old', 1, 2, 'codex/discord-111111111111111111', 'Original')
            class Interaction:
                id = 123456789012345678
                guild_id = 1479878482438918297
                channel_id = 2
                user = type('User', (), {'id': 3})()
            with self.assertRaisesRegex(ValueError, 'original requester'):
                await bot.start_job(Interaction(), 'Change something small', 'old')
            self.assertEqual(bot.store.db.execute('SELECT count(*) FROM charges').fetchone()[0], 0)
            bot.store.db.close()

    async def test_uncertain_dispatch_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot = Bot()
            bot.github = type('Github', (), {'dispatch': AsyncMock(side_effect=TimeoutError)})()
            class Interaction:
                id = 123456789012345678
                guild_id = 1479878482438918297
                channel_id = 2
                user = type('User', (), {'id': 3})()
            result = await bot.start_job(Interaction(), 'Change something small')
            self.assertIn('accepted', result)
            self.assertEqual(bot.store.job(str(Interaction.id))['status'], 'dispatching')
            self.assertEqual(bot.github.dispatch.call_count, 1)
            bot.store.db.close()


class WorkerTests(unittest.TestCase):
    def test_edit_tools_do_not_execute_and_complete_before_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            ws = worker.Workspace(folder)
            replies = [
                {'output': [{'type': 'function_call', 'name': 'replace', 'call_id': 'c',
                             'arguments': json.dumps({'path': 'src/Test.cs', 'old': '', 'new': 'class Test {}'})}]},
                {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Added Test.'}]}]}
            ]
            with patch.object(worker, 'request_json', side_effect=replies):
                summary, reserved = worker.generate(ws, 'Small feature', 'fake')
            self.assertEqual(summary, 'Added Test.')
            self.assertLess(reserved, worker.CAP)
            self.assertEqual(ws.changed, {'src/Test.cs'})

    def test_oversized_prompt_cannot_start_billable_call(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker, 'request_json') as request:
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                worker.generate(worker.Workspace(folder), 'x' * 200000, 'fake')
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
