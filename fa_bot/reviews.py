"""User-confirmed PR creation and durable tracking of the shared branch review."""
import asyncio
import logging
import discord
from .channel_policy import ignore_interaction
from .messages import action_message, notify_owner

LOG = logging.getLogger('fa_bot')


class PackageReview(discord.ui.View):
    def __init__(self, bot, key, sources=None):
        super().__init__(timeout=None)
        self.bot, self.key, self.pending = bot, key, False
        self.button = discord.ui.Button(label='This build meets my requirements - Create PR', style=discord.ButtonStyle.success,
                                        custom_id='fa-tested-pr:' + key)
        self.button.callback = self.confirm
        self.add_item(self.button)
        if sources:
            for item in list(sources.children):
                sources.remove_item(item)
                item.row = 1
                self.add_item(item)

    async def confirm(self, interaction):
        if await ignore_interaction(interaction):
            return
        if self.pending:
            await interaction.response.send_message('PR confirmation is being processed.', ephemeral=True)
            return
        self.pending = True
        try:
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                result = await request_pr(self.bot, interaction, self.key)
            except ValueError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            self.button.disabled = True
            await interaction.followup.send(result, ephemeral=True)
            if interaction.message:
                await interaction.message.edit(view=self)
        finally:
            self.pending = False


async def request_pr(bot, interaction, key):
    job = bot.store.job(key)
    review = bot.store.review(key)
    if not job or job['owner'] != str(interaction.user.id) or job['channel'] != str(interaction.channel_id):
        raise ValueError('Only the original requester in the original channel can confirm testing.')
    if job['status'] != 'success' or not review:
        raise ValueError('A successful package and its source receipt are required.')
    if bot.store.latest_job()['id'] != key:
        raise ValueError('The shared branch has moved on; test the latest package instead.')
    if review['state'] != 'ready' or bot.store.repair_active():
        raise ValueError('A repair or PR is already pending. Please wait for maintainer review.')
    bot.store.review_state(key, 'requested')
    try:
        await bot.github.dispatch_pr(job, review['sha'])
    except Exception as error:
        # Dispatch may have succeeded; retain the slot and discover the workflow.
        LOG.warning('PR dispatch outcome uncertain: %s', type(error).__name__)
    return 'I have requested a PR for your tested changes. Automated checks come next; only the maintainer can approve and merge it.'


async def poll_reviews(bot):
    await bot.wait_until_ready()
    while True:
        for review in bot.store.active_reviews():
            key = review['job_id']
            try:
                if review['state'] == 'opened':
                    pr = await bot.github.public_pulls(review['number'])
                    if pr['state'] == 'closed':
                        bot.store.review_state(key, 'merged' if pr.get('merged') else 'closed')
                    continue
                run = await bot.github.find_run(key, review=True)
                if not run or run['status'] != 'completed':
                    continue
                job = bot.store.job(key)
                if run['conclusion'] == 'success':
                    prs = await bot.github.public_pulls()
                    found = [p for p in prs if p['head']['sha'] == review['sha']]
                    if len(found) != 1:
                        bot.store.review_state(key, 'uncertain')
                        continue
                    pr = found[0]
                    bot.store.review_state(key, 'opened', pr['html_url'], pr['number'])
                    payload = action_message(f'PR #{pr["number"]} has been created! Your tested changes are ready for automated checks and maintainer review. Only the maintainer can merge them.', 'View PR', pr['html_url'])
                else:
                    bot.store.review_state(key, 'failed')
                    payload = action_message('I could not create the PR. Please ask the maintainer to check the issue. Your changes have not been merged.')
                channel = bot.get_channel(int(job['channel'])) or await bot.fetch_channel(int(job['channel']))
                await channel.send(**notify_owner(payload, job['owner']))
            except Exception as error:
                LOG.warning('PR tracking failed for %s: %s', key, type(error).__name__)
        await asyncio.sleep(20)
