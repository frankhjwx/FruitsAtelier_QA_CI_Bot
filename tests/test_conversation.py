import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager

from fa_bot.bot import Bot
from fa_bot.conversation import reply_context
from fa_bot.qa import QAReply, answer
from test_flow import Session, Knowledge


def message(mid, uid, content, channel, parent=None):
    return NS(id=mid, author=NS(id=uid, bot=uid==9), content=content, channel=channel,
              reference=NS(message_id=parent.id, channel_id=channel.id, resolved=parent) if parent else None)


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_chain_is_chronological_and_bounded(self):
        channel=NS(id=10)
        parent=None
        for i in range(10):
            parent=message(i+1, 1 if i%2==0 else 9, 'x'*1500, channel, parent)
        current=message(20,1,'Follow up',channel,parent)
        triggered, context=await reply_context(current,9)
        self.assertTrue(triggered)
        self.assertEqual(len(context),6)
        self.assertEqual([x['role'] for x in context], ['user','assistant']*3)
        self.assertEqual(sum(len(x['content']) for x in context),6000)

    async def test_other_authors_and_channels_are_not_followed(self):
        channel=NS(id=10)
        other=message(1,2,'Other user content',channel)
        parent=message(2,9,'Shared answer',channel,other)
        current=message(3,1,'Follow up',channel,parent)
        triggered,context=await reply_context(current,9)
        self.assertTrue(triggered)
        self.assertEqual(context,[{'role':'assistant','content':'Shared answer'}])
        current.reference.channel_id=11
        self.assertEqual(await reply_context(current,9),(False,[]))

    async def test_reply_without_mention_triggers_contextual_qa(self):
        @asynccontextmanager
        async def typing():
            yield
        channel=NS(id=10,typing=typing)
        parent=message(1,9,'FSlider anchors can be edited.',channel)
        current=message(2,1,'How do I do that?',channel,parent)
        current.guild=NS(id=1479878482438918297)
        current.mentions=[]
        current.reply=AsyncMock()
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ',{'STATE_FILE':str(Path(folder)/'state.sqlite3')}):
            bot=Bot()
            bot._connection.user=NS(id=9)
            bot.ask=AsyncMock(return_value=QAReply('Follow-up answer'))
            try:
                await bot.on_message(current)
                self.assertEqual(bot.ask.call_args.kwargs['context'],[{'role':'assistant','content':parent.content}])
                self.assertTrue(current.reply.call_args.kwargs['suppress_embeds'])
                self.assertTrue(bot.intents.message_content)
            finally:
                bot.store.db.close()

    async def test_context_is_sent_before_current_question(self):
        session=Session()
        await answer(session,'fake',Knowledge(),'How?',context=[{'role':'user','content':'FSlider anchors'}])
        self.assertEqual(session.calls[0]['input'][0],{'role':'user','content':'FSlider anchors'})
        self.assertEqual(session.calls[0]['input'][1],{'role':'user','content':'How?'})
