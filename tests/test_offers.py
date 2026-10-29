import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from fa_bot.messages import DevelopmentOffer, qa_message
from fa_bot.qa import QAReply


def interaction(user=1):
    return NS(id=123, user=NS(id=user), channel_id=2,
              response=NS(send_message=AsyncMock(), defer=AsyncMock()),
              followup=NS(send=AsyncMock()), message=NS(edit=AsyncMock()))


class OfferTests(unittest.IsolatedAsyncioTestCase):
    async def test_qa_does_not_dispatch_until_owner_confirms(self):
        bot=NS(start_job=AsyncMock(return_value='Task accepted'), store=NS(job=lambda key: None))
        data=qa_message(QAReply('Proposed feature.', 'Add a small feature requested by the user.'),bot,1,2)
        view=data['view']
        bot.start_job.assert_not_called()
        await view.confirm(interaction(9))
        bot.start_job.assert_not_called()
        event=interaction()
        await view.confirm(event)
        bot.start_job.assert_awaited_once_with(event,view.request)
        self.assertTrue(view.confirm_button.disabled)
        await view.confirm(interaction())
        self.assertEqual(bot.start_job.await_count,1)

    async def test_concurrent_confirmation_dispatches_once(self):
        entered=asyncio.Event()
        release=asyncio.Event()
        async def start(*args):
            entered.set()
            await release.wait()
            return 'Task accepted'
        bot=NS(start_job=AsyncMock(side_effect=start), store=NS(job=lambda key: None))
        view=DevelopmentOffer(bot,1,2,'Add a small requested feature.')
        first=asyncio.create_task(view.confirm(interaction()))
        await entered.wait()
        await view.confirm(interaction())
        release.set()
        await first
        self.assertEqual(bot.start_job.await_count,1)

    async def test_busy_rejection_keeps_confirmation_available(self):
        bot=NS(start_job=AsyncMock(side_effect=ValueError('A repair is already running.')))
        view=DevelopmentOffer(bot,1,2,'Fix the reported defect.')
        event=interaction()
        await view.confirm(event)
        self.assertFalse(view.submitted)
        self.assertFalse(view.pending)
        self.assertFalse(view.confirm_button.disabled)
        event.followup.send.assert_awaited_once_with('A repair is already running.',ephemeral=True)

    async def test_ordinary_answer_has_no_development_button(self):
        data=qa_message(QAReply('Read [manual](https://github.com/example/repo).'),NS(),1,2)
        self.assertEqual(len(data['view'].children),1)
        self.assertIsNotNone(data['view'].children[0].url)
        self.assertNotIn('https://',data['content'])


    async def test_qa_cooldown_does_not_block_immediate_confirmation(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from fa_bot.bot import Bot
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'STATE_FILE':str(Path(folder)/'state.sqlite3')}):
            bot=Bot()
            bot.github=NS(dispatch=AsyncMock())
            try:
                bot.admit(1)
                event=NS(id=123456789012345,user=NS(id=1),channel_id=2,guild_id=1554882915945414726)
                bot.knowledge.sha='a'*40
                result=await bot.start_job(event,'Add the requested feature.', localization={'query':'selection time', 'direction':'Inspect selection', 'commit':'a'*40, 'locations':[{'path':'src/Test.cs','line':1}]})
                self.assertIn('accepted',result)
                bot.github.dispatch.assert_awaited_once()
            finally:
                bot.store.db.close()
