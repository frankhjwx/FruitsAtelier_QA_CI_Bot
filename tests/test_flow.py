import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from fa_bot.bot import Bot
from fa_bot.github import GitHub
from fa_bot.qa import answer, QAReply
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
        return Response({'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'answer': 'Grounded answer', 'change_request': None})}]}],
                         'usage': {'input_tokens': 1000, 'output_tokens': 100}})


class Knowledge:
    sha = 'abc'
    updated = '2026-10-29'

    def search(self, query):
        return [{'url': 'https://example.com/evidence', 'text': 'Slider anchors'}]


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_build_routing_uses_editor_repository(self):
        github = GitHub(None, 'fake', 'frankhjwx/FruitsAtelier_QA_CI_Bot')
        github.request = AsyncMock(return_value={'workflow_runs': [
            {'display_title': 'FA preview 123', 'id': 4},
            {'display_title': 'FA task 123', 'id': 5}]})
        found = await github.find_run('123', build=True)
        self.assertEqual(found['id'], 4)
        self.assertIn('/repos/frankhjwx/FruitsAtelier/actions/workflows/discord-preview.yml/',
                      github.request.call_args.args[1])
        await github.run(4, build=True)
        self.assertEqual(github.request.call_args.args[1], '/repos/frankhjwx/FruitsAtelier/actions/runs/4')

    async def test_download_targets_matching_unexpired_package(self):
        github = GitHub(None, 'fake', 'bot/repo')
        github.request = AsyncMock(return_value={'artifacts': [
            {'id': 1, 'name': 'source-123', 'expired': False},
            {'id': 2, 'name': 'windows-experimental-456', 'expired': False},
            {'id': 3, 'name': 'windows-experimental-123', 'expired': False}]})
        self.assertEqual(await github.package_url(20, '123'),
                         'https://github.com/frankhjwx/FruitsAtelier/actions/runs/20/artifacts/3')
        github.request.return_value['artifacts'][-1]['expired'] = True
        with self.assertRaises(ValueError):
            await github.package_url(20, '123')

    async def test_edit_completion_tracks_public_build_before_notification(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot = Bot()
            bot.wait_until_ready = AsyncMock()
            bot.store.add_job('123', 1, 2, 'codex/discord-111111111111111111', 'Small fix')
            bot.store.update('123', 'running', 10)
            github = type('Github', (), {
                'run': AsyncMock(return_value={'id': 10, 'status': 'completed', 'conclusion': 'success', 'html_url': 'private'}),
                'find_run': AsyncMock(return_value={'id': 20, 'html_url': 'public'}),
                'package_url': AsyncMock(return_value='https://github.com/test/artifacts/123'),
                'source_receipt': AsyncMock(return_value='a'*40)})()
            bot.github = github
            with patch('fa_bot.bot.asyncio.sleep', side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await bot.poll_jobs()
            self.assertEqual(bot.store.job('123')['status'], 'building')
            self.assertEqual(bot.store.job('123')['run_id'], 20)
            github.run.return_value = {'id': 20, 'status': 'completed', 'conclusion': 'success', 'html_url': 'public'}
            channel = type('Channel', (), {'send': AsyncMock()})()
            with patch.object(bot, 'get_channel', return_value=channel), patch('fa_bot.bot.asyncio.sleep', side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await bot.poll_jobs()
            github.run.assert_awaited_with(20, build=True)
            self.assertEqual(bot.store.job('123')['status'], 'success')
            self.assertIn('https://github.com/test/artifacts/123', [item.url for item in channel.send.call_args.kwargs['view'].children])
            self.assertNotIn('private', channel.send.call_args.kwargs['content'])
            bot.store.db.close()

    async def test_qa_two_rounds_evidence_and_usage(self):
        session = Session()
        text, cost = await answer(session, 'fake', Knowledge(), 'How do anchors work?')
        self.assertEqual(text.text, 'Grounded answer')
        self.assertIsNone(text.change_request)
        self.assertEqual(len(session.calls), 2)
        self.assertLess(cost, .02)
        self.assertIn('evidence', session.calls[1]['input'][-1]['output'])
        self.assertFalse(session.calls[0]['store'])

    async def test_revision_requires_owner_and_channel(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot = Bot()
            bot.store.add_job('old', 1, 2, 'codex-discord-bug-fix', 'Original')
            bot.store.update('old', 'success')
            class Interaction:
                id = 123456789012345678
                guild_id = 1479878482438918297
                channel_id = 2
                user = type('User', (), {'id': 3})()
            with self.assertRaisesRegex(ValueError, 'original requester'):
                await bot.start_job(Interaction(), 'Change something small', 'old')
            self.assertEqual(bot.store.db.execute('SELECT count(*) FROM charges').fetchone()[0], 0)
            bot.store.db.close()

    async def test_shared_branch_rejects_a_second_active_repair(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot = Bot()
            try:
                bot.store.add_job('active', 1, 2, 'codex-discord-bug-fix', 'Original')
                bot.store.update('active', 'build_dispatch_uncertain')
                interaction = type('Interaction', (), {'id': 123456789012345678,
                    'guild_id': 1479878482438918297, 'channel_id': 2,
                    'user': type('User', (), {'id': 3})()})()
                with self.assertRaisesRegex(ValueError, 'already running'):
                    await bot.start_job(interaction, 'Change something small')
                self.assertEqual(bot.store.db.execute('SELECT count(*) FROM charges').fetchone()[0], 0)
            finally:
                bot.store.db.close()

    async def test_only_latest_task_can_continue_the_shared_branch(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot = Bot()
            bot.github = type('Github', (), {'dispatch': AsyncMock()})()
            try:
                for key in ('old', 'latest'):
                    bot.store.add_job(key, 3, 2, 'codex-discord-bug-fix', 'Original request')
                    bot.store.update(key, 'success')
                interaction = type('Interaction', (), {'id': 123456789012345678,
                    'guild_id': 1479878482438918297, 'channel_id': 2,
                    'user': type('User', (), {'id': 3})()})()
                with self.assertRaisesRegex(ValueError, 'latest task'):
                    await bot.start_job(interaction, 'Add a small clarification', 'old')
                bot.github.dispatch.assert_not_awaited()
                bot.cooldowns.clear()
                bot.ask = AsyncMock(return_value=QAReply('Located', 'Original request follow up', localization={'query':'selection time', 'direction':'Inspect selection', 'commit':'a'*40, 'locations':[{'path':'src/Test.cs','line':1}]}))
                result = await bot.start_job(interaction, '  Retry  ', 'latest')
                self.assertIn('accepted', result)
                args = bot.github.dispatch.call_args.args
                self.assertEqual(args[2], 'codex-discord-bug-fix')
                self.assertIn('Original request', args[1])
                self.assertIn('Follow-up:\nRetry', json.loads(args[1])['request'])
            finally:
                bot.store.db.close()

    async def test_revise_command_defaults_to_latest_task(self):
        from types import SimpleNamespace as NS
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot=Bot()
            try:
                bot.store.add_job('latest',3,2,'codex-discord-bug-fix','Original request')
                bot.start_job=AsyncMock(return_value='Accepted')
                interaction=NS(id=123, response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))
                command=bot.tree.get_command('revise')
                await command.callback(interaction,feedback='Retry')
                bot.start_job.assert_awaited_once_with(interaction,'Retry','latest')
                self.assertFalse(next(p for p in command.parameters if p.name=='task_id').required)
            finally:
                bot.store.db.close()

    async def test_region_failure_has_actionable_message_without_provider_body(self):
        from fa_bot.qa import APIError
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot = Bot()
            bot.http_session = None
            bot.api_key = 'fake'
            try:
                with patch('fa_bot.bot.answer', side_effect=APIError(403, 'unsupported_country_region_territory')):
                    result = await bot.ask(1, 123, 'Introduce Fruits Atelier')
                self.assertIn('maintainer must update', result.text)
                self.assertEqual(bot.busy, 0)
            finally:
                bot.store.db.close()

    async def test_another_repair_starting_during_qa_prevents_dispatch(self):
        from types import SimpleNamespace as NS
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STATE_FILE': str(Path(folder)/'state.sqlite3')}):
            bot=Bot(); bot.github=NS(dispatch=AsyncMock())
            async def locate(*args, **kwargs):
                bot.store.add_job('other',4,2,'codex-discord-bug-fix','Other request')
                return QAReply('Located','Display timestamps',localization={
                    'query':'selection','direction':'Inspect selection','commit':'a'*40,
                    'locations':[{'path':'src/Test.cs','line':1}]})
            bot.ask=locate
            try:
                event=NS(id=1234567890123456,user=NS(id=3),channel_id=2,guild_id=1479878482438918297)
                with self.assertRaisesRegex(ValueError,'already running'):
                    await bot.start_job(event,'Display selected timestamps')
                bot.github.dispatch.assert_not_awaited()
                self.assertIsNone(bot.store.job(str(event.id)))
            finally:
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
            bot.ask = AsyncMock(return_value=QAReply('Located', 'Change something small', localization={'query':'selection time', 'direction':'Inspect selection', 'commit':'a'*40, 'locations':[{'path':'src/Test.cs','line':1}]}))
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
            with self.assertRaisesRegex(RuntimeError, 'context limit'):
                worker.generate(worker.Workspace(folder), 'x' * 300000, 'fake')
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
