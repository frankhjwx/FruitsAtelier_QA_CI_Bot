"""Owner-scoped cancellation of editing, private validation and package work."""
import asyncio
import time

import discord

from .channel_policy import ignore_interaction, ignored_channel
from .transfer import revoke_transfer


class CancelTask(discord.ui.View):
    def __init__(self, bot, key):
        super().__init__(timeout=None)
        self.bot,self.key=bot,key
        button=discord.ui.Button(label='Cancel task',style=discord.ButtonStyle.danger,
                                 custom_id='fa-cancel-task:'+key)
        button.callback=self.cancel
        self.add_item(button)

    async def cancel(self, interaction):
        if await ignore_interaction(interaction): return
        await interaction.response.defer(thinking=True,ephemeral=True)
        try:
            text=await request_cancel(self.bot,interaction,self.key)
        except ValueError as error:
            text=str(error)
        await interaction.followup.send(text,ephemeral=True)


async def request_cancel(bot, interaction, key):
    bot.store.request_cancel(key,interaction.user.id,interaction.channel_id)
    await asyncio.to_thread(revoke_transfer,key)
    from .progress import update_progress
    await update_progress(bot,bot.store.job(key),force=True)
    return 'Cancellation requested. I am stopping the associated GitHub jobs; this may take a moment.'


async def reconcile_cancel(bot, job):
    key=job['id']; record=bot.store.cancellation(key)
    await asyncio.to_thread(revoke_transfer,key)
    stopped,parent_found=await bot.github.cancel_task(key,record['run_id'],bool(record['building']))
    if job['status']=='cancelled': return
    if not stopped or (not parent_found and time.time()-record['created']<600):
        from .progress import update_progress
        bot.store.update(key,'cancelling')
        await update_progress(bot,bot.store.job(key))
        return
    from .messages import action_message, notify_owner
    from .progress import edit_progress
    payload=action_message('Task cancelled. Associated GitHub jobs have stopped. Any source already published is retained; no PR will be created for this cancelled task.')
    # The bound message can be edited idempotently after a restart.
    if not await edit_progress(bot,job,payload):
        channel=bot.get_channel(int(job['channel'])) or await bot.fetch_channel(int(job['channel']))
        if not ignored_channel(channel):
            await channel.send(**notify_owner(payload,job['owner']))
    bot.store.update(key,'cancelled')
