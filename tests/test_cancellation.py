import asyncio
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fa_bot.cancellation import CancelTask, request_cancel, reconcile_cancel
from fa_bot.github import GitHub
from fa_bot.store import Store

KEY='1234567890123456'


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'state.sqlite3'
        self.store=Store(self.path)
        self.store.add_job(KEY,1,2,'codex-discord-bug-fix','Fix requested behavior')
        self.store.update(KEY,'running',10)
        self.bot=NS(store=self.store)
        self.owner=NS(user=NS(id=1),channel_id=2)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    async def test_owner_channel_and_terminal_boundaries(self):
        for owner,channel in ((9,2),(1,9)):
            event=NS(user=NS(id=owner),channel_id=channel)
            with patch('fa_bot.cancellation.revoke_transfer') as revoke:
                with self.assertRaisesRegex(ValueError,'original requester'):
                    await request_cancel(self.bot,event,KEY)
                revoke.assert_not_called()
        self.assertIsNone(self.store.cancellation(KEY))
        for status in ('success','failure','notifying','cancelled'):
            self.store.update(KEY,status)
            with self.assertRaises(ValueError):self.store.request_cancel(KEY,1,2)

    async def test_cancellation_is_durable_and_slot_is_held_until_runners_stop(self):
        with patch('fa_bot.cancellation.revoke_transfer') as revoke, patch('fa_bot.progress.update_progress',new_callable=AsyncMock):
            await request_cancel(self.bot,self.owner,KEY)
            await request_cancel(self.bot,self.owner,KEY)
        self.assertEqual(revoke.call_count,2)
        self.assertTrue(self.store.repair_active())
        self.store.db.close(); self.store=Store(self.path); self.bot.store=self.store
        self.assertEqual(self.store.job(KEY)['status'],'cancelling')
        self.bot.github=NS(cancel_task=AsyncMock(return_value=(False,True)))
        with patch('fa_bot.cancellation.revoke_transfer'),patch('fa_bot.progress.update_progress',new_callable=AsyncMock):
            await reconcile_cancel(self.bot,self.store.job(KEY))
        self.assertEqual(self.store.job(KEY)['status'],'cancelling')
        self.bot.github.cancel_task.return_value=(True,True)
        with patch('fa_bot.cancellation.revoke_transfer'),patch('fa_bot.progress.edit_progress',new_callable=AsyncMock,return_value=True) as edit:
            await reconcile_cancel(self.bot,self.store.job(KEY))
            await reconcile_cancel(self.bot,self.store.job(KEY))
        edit.assert_awaited_once()
        self.assertEqual(self.store.job(KEY)['status'],'cancelled')
        self.assertFalse(self.store.repair_active())
        self.assertEqual(self.bot.github.cancel_task.await_count,3)

    async def test_missing_dispatch_is_not_declared_cancelled_immediately(self):
        self.store.request_cancel(KEY,1,2)
        self.bot.github=NS(cancel_task=AsyncMock(return_value=(True,False)))
        with patch('fa_bot.cancellation.revoke_transfer'),patch('fa_bot.progress.update_progress',new_callable=AsyncMock):
            await reconcile_cancel(self.bot,self.store.job(KEY))
        self.assertEqual(self.store.job(KEY)['status'],'cancelling')

    async def test_progress_button_uses_the_same_owner_check(self):
        event=NS(user=NS(id=9),channel_id=2,response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))
        view=CancelTask(self.bot,KEY)
        await view.cancel(event)
        self.assertIn('original requester',event.followup.send.call_args.args[0])
        self.assertTrue(event.followup.send.call_args.kwargs['ephemeral'])
        self.assertIsNone(self.store.cancellation(KEY))

    async def test_command_defaults_to_latest_and_routes_through_owner_check(self):
        from fa_bot.bot import Bot
        with patch.dict(os.environ,{'STATE_FILE':str(self.path)}):
            bot=Bot()
        try:
            event=NS(response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))
            with patch('fa_bot.bot.request_cancel',new_callable=AsyncMock,return_value='Cancelling') as cancel:
                await bot.tree.get_command('cancel').callback(event)
            cancel.assert_awaited_once_with(bot,event,KEY)
        finally:
            bot.store.db.close()


class GitHubCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_cancel_response_has_no_json_body(self):
        response=NS(status=202,json=AsyncMock(side_effect=AssertionError('No JSON body')))
        context=AsyncMock(); context.__aenter__.return_value=response
        session=NS(request=MagicMock(return_value=context))
        github=GitHub(session,'test','bot/repo')
        self.assertIsNone(await github.request('POST','/repos/bot/repo/actions/runs/10/cancel'))
        response.json.assert_not_awaited()

    async def test_cancels_parent_validation_and_package_but_no_other_task(self):
        github=GitHub(None,'test','bot/repo')
        parent={'id':10,'status':'in_progress'}
        preview={'id':20,'status':'queued'}
        github.run=AsyncMock(return_value=parent)
        github.find_run=AsyncMock(return_value=preview)
        data={'workflow_runs':[
            {'id':30,'status':'in_progress','display_title':f'FA validation {KEY}.1'},
            {'id':31,'status':'completed','display_title':f'FA validation {KEY}.2'},
            {'id':99,'status':'in_progress','display_title':'FA validation other.1'}]}
        github.request=AsyncMock(side_effect=[data,None,None,None])
        stopped,found=await github.cancel_task(KEY,10,False)
        self.assertFalse(stopped); self.assertTrue(found)
        posts=[call.args[1] for call in github.request.call_args_list if call.args[0]=='POST']
        self.assertEqual(posts,['/repos/bot/repo/actions/runs/10/cancel',
                               '/repos/bot/repo/actions/runs/30/cancel',
                               '/repos/frankhjwx/FruitsAtelier/actions/runs/20/cancel'])
        github.run.return_value={**parent,'status':'completed'}
        github.find_run.return_value=None
        github.request=AsyncMock(return_value={'workflow_runs':[]})
        self.assertEqual(await github.cancel_task(KEY,10,False),(True,True))
        self.assertEqual(github.request.await_count,1)
