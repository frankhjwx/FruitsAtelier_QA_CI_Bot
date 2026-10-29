import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from discord import InteractionType
from fa_bot.channel_policy import ignored_channel, ignore_interaction
from fa_bot.bot import Bot


def channel(name='information', category='Announcements and Infos'):
    return NS(name=name, category=NS(name=category))


class ChannelPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_only_named_announcement_channels_and_their_threads_are_ignored(self):
        for name in ('information','releases','arrivals','tutorials','todo'):
            self.assertTrue(ignored_channel(channel(name)))
            self.assertTrue(ignored_channel(NS(name='discussion',parent=channel(name))))
        self.assertFalse(ignored_channel(channel('general')))
        self.assertFalse(ignored_channel(NS(name='todo',category=channel().category,parent=channel('general'))))
        self.assertFalse(ignored_channel(channel(category='Support')))
        self.assertFalse(ignored_channel(None))

    async def test_mention_is_ignored_before_context_or_paid_qa(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{'STATE_FILE':str(Path(folder)/'state.sqlite3')}):
            bot=Bot(); bot.ask=AsyncMock()
            try:
                message=NS(channel=channel(),reply=AsyncMock())
                with patch('fa_bot.bot.reply_context',new_callable=AsyncMock) as context:
                    await bot.on_message(message)
                context.assert_not_awaited(); bot.ask.assert_not_awaited(); message.reply.assert_not_awaited()
                self.assertEqual(bot.store.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
            finally:
                bot.store.db.close()

    async def test_commands_and_old_buttons_do_not_start_work_or_reply(self):
        for kind in (InteractionType.application_command,InteractionType.component):
            event=NS(channel=channel('todo'),type=kind,response=NS(defer=AsyncMock()),delete_original_response=AsyncMock())
            self.assertTrue(await ignore_interaction(event))
            event.response.defer.assert_awaited_once_with(ephemeral=True,thinking=kind==InteractionType.application_command)
            self.assertEqual(event.delete_original_response.await_count,int(kind==InteractionType.application_command))
