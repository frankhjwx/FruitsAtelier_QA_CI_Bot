import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch, create_autospec
import discord

from fa_bot.progress import bind_progress, edit_progress, progress_text, update_progress
from fa_bot.store import Store


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.store.add_job('123456789012345', 1, 2, 'branch', 'request')
        self.message = NS(id=99, edit=AsyncMock())
        self.channel = NS(parent=None, category=None, name='support', get_partial_message=lambda mid: self.message)
        self.bot = NS(store=self.store, get_channel=lambda cid: self.channel, fetch_channel=AsyncMock())

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    async def test_throttle_restart_and_final_edit_same_message(self):
        key = '123456789012345'
        with patch('fa_bot.progress.time.time', return_value=1000):
            self.assertTrue(await bind_progress(self.bot, key, self.message))
        self.message.edit.assert_awaited_once()
        self.store.db.close()
        self.store = Store(self.path)
        self.bot.store = self.store
        with patch('fa_bot.progress.time.time', return_value=1019):
            await update_progress(self.bot, self.store.job(key))
        self.assertEqual(self.message.edit.await_count, 1)
        with patch('fa_bot.progress.time.time', return_value=1020):
            await update_progress(self.bot, self.store.job(key))
        self.assertEqual(self.message.edit.await_count, 2)
        await edit_progress(self.bot, self.store.job(key), {'content': 'Complete', 'view': None})
        self.assertEqual(self.message.edit.call_args.kwargs['content'], 'Complete')
        self.assertFalse(self.message.edit.call_args.kwargs['allowed_mentions'].everyone)

    async def test_failed_edit_is_throttled_without_replacement(self):
        self.message.edit.side_effect = RuntimeError('Unavailable')
        key = '123456789012345'
        with patch('fa_bot.progress.time.time', return_value=1000):
            await bind_progress(self.bot, key, self.message)
            await update_progress(self.bot, self.store.job(key))
        self.message.edit.assert_awaited_once()
        self.assertEqual(self.store.progress(key)['message_id'], '99')

    async def test_progress_and_completion_use_real_partial_message_edit_signature(self):
        self.message=create_autospec(discord.PartialMessage,instance=True)
        self.message.id=99
        key='123456789012345'
        await bind_progress(self.bot,key,self.message)
        self.message.edit.assert_awaited_once()
        self.assertIn('estimated',self.message.edit.call_args.kwargs['content'].lower())
        await edit_progress(self.bot,self.store.job(key),{
            'content':'Your test build is ready.', 'view':None, 'suppress_embeds':True})
        self.assertEqual(self.message.edit.await_count,2)
        self.assertNotIn('suppress_embeds',self.message.edit.call_args.kwargs)

    def test_live_stage_repair_attempt_and_private_data_boundary(self):
        folder=Path(self.temp.name)
        (folder/'progress-edit.json').write_text(json.dumps({'phase':'waiting_validation','attempt':2}))
        (folder/'progress-validation-2.json').write_text(json.dumps({'phase':'fixed','attempt':2,'private':'sample content'}))
        job={'id':'123456789012345','status':'running','created':0}
        with patch('fa_bot.progress.locate',return_value=(folder,{})):
            text=progress_text(job,125)
        self.assertIn('changed version against the same test',text)
        self.assertIn('attempt 2/2',text)
        self.assertNotIn('sample content',text)

    def test_eta_queue_overrun_and_history(self):
        from fa_bot.progress import remaining_text
        job={'status':'queued','created':0}
        self.assertIn('Queue wait is unknown',remaining_text(job,9999))
        job['status']='running'
        self.assertIn('5–15 min',remaining_text(job,300,(600,1200),0))
        self.assertIn('recent successful runs',remaining_text(job,300,(600,1200),0))
        self.assertIn('no reliable remaining time',remaining_text(job,1201,(600,1200),0))
        job['status']='dispatch_uncertain'
        self.assertIn('unavailable',remaining_text(job,300))

    async def test_ignored_channel_never_edited(self):
        self.channel.name = 'todo'
        self.channel.category = NS(name='Announcements and Infos')
        await bind_progress(self.bot, '123456789012345', self.message)
        self.message.edit.assert_not_awaited()

    async def test_declined_request_does_not_bind_message(self):
        self.assertFalse(await bind_progress(self.bot, 'missing', self.message))
        self.message.edit.assert_not_awaited()

    async def test_completed_package_replaces_progress_in_poll_loop(self):
        from fa_bot.bot import Bot
        with patch.dict('os.environ', {'STATE_FILE': str(self.path)}):
            bot = Bot()
        try:
            key = '123456789012345'
            bot.store.bind_progress(key, 99)
            bot.store.update(key, 'building', 10)
            bot.github = NS(run=AsyncMock(return_value={'status': 'completed', 'conclusion': 'success', 'id': 10, 'html_url': 'public'}),
                            package_url=AsyncMock(return_value='https://github.com/test/package'))
            bot.wait_until_ready = AsyncMock()
            self.channel.send = AsyncMock()
            with patch.object(bot, 'get_channel', return_value=self.channel), patch('fa_bot.bot.asyncio.sleep', side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await bot.poll_jobs()
            self.assertEqual(bot.store.job(key)['status'], 'success')
            self.channel.send.assert_not_awaited()
            self.message.edit.assert_awaited_once()
            self.assertIn('Your test build is ready', self.message.edit.call_args.kwargs['content'])
        finally:
            bot.store.db.close()

    async def test_latest_attempt_does_not_reuse_previous_success(self):
        folder = Path(self.temp.name)
        (folder / 'candidate-1.json').write_text('{}')
        (folder / 'candidate-2.json').write_text('{}')
        (folder / 'result-1-windows.json').write_text(json.dumps({'passed': True}))
        (folder / 'result-2-windows.json').write_text(json.dumps({'passed': False, 'output': 'private material'}))
        job = {'id': '123456789012345', 'status': 'running', 'created': 0}
        with patch('fa_bot.progress.locate', return_value=(folder, {})):
            text = progress_text(job, 125)
        self.assertIn('attempt 2', text)
        self.assertIn('windows: not passed', text)
        self.assertNotIn('macos:', text)
        self.assertNotIn('private material', text)
        self.assertIn('2m 05s', text)
