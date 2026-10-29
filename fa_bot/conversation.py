"""Read bounded context from the current user's explicit Discord reply chain."""
import asyncio
import discord


async def reply_context(message, bot_id):
    context = []
    is_reply = False
    current = message
    seen = {message.id}
    try:
        async with asyncio.timeout(3):
            for _ in range(6):
                ref = current.reference
                if not ref or not ref.message_id or ref.channel_id != message.channel.id:
                    break
                if ref.message_id in seen:
                    break
                seen.add(ref.message_id)
                parent = ref.resolved
                if not hasattr(parent, 'author'):
                    parent = await message.channel.fetch_message(ref.message_id)
                if parent.channel.id != message.channel.id:
                    break
                if current is message:
                    is_reply = parent.author.id == bot_id
                    if not is_reply:
                        break
                if parent.author.id not in (bot_id, message.author.id):
                    break
                if parent.content:
                    context.append({'role': 'assistant' if parent.author.id == bot_id else 'user',
                                    'content': parent.content[:1000]})
                current = parent
    except (TimeoutError, discord.HTTPException):
        pass
    return is_reply, list(reversed(context))
