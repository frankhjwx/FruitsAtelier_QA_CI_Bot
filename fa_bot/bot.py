import asyncio
import contextlib
import logging
import os
import re
import time

import aiohttp
import discord
from discord import app_commands

from .conversation import reply_context
from .config import GUILDS, OWNER, JOB_CAP, WORK_BRANCH, secret
from .github import GitHub
from .knowledge import Knowledge
from .messages import linked_message
from .qa import APIError, answer
from .store import Store

LOG = logging.getLogger('fa_bot')
TEMPLATE = ('Usage: version, OS, goal, what you tried.\n'
            'Bug: version, steps, expected vs actual, sanitized example.\n'
            'Feature: use case, desired behavior, one concrete example.')


class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.store = Store(os.getenv('STATE_FILE', 'state/bot.sqlite3'))
        self.knowledge = Knowledge()
        self.cooldowns = {}
        self.busy = 0
        self.workers = []
        self.register_commands()

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        self.github = GitHub(self.http_session, secret('GITHUB_TOKEN'), os.environ['BOT_REPO'])
        self.api_key = secret('OPENAI_API_KEY')
        for guild in GUILDS:
            self.tree.copy_global_to(guild=discord.Object(id=guild))
            try:
                await self.tree.sync(guild=discord.Object(id=guild))
            except discord.Forbidden:
                LOG.warning('Command registration unavailable for guild %s; check the bot invitation', guild)
        self.workers = [asyncio.create_task(self.sync_knowledge()), asyncio.create_task(self.poll_jobs())]

    async def close(self):
        for task in self.workers:
            task.cancel()
        for task in self.workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if hasattr(self, 'http_session'):
            await self.http_session.close()
        await super().close()

    def eligible(self, user, guild):
        return (guild is None and user == OWNER) or (guild is not None and guild in GUILDS)

    def admit(self, user):
        now = time.monotonic()
        if now - self.cooldowns.get(user, -100) < 10:
            raise ValueError('Please wait 10 seconds before another request.')
        if self.busy >= 3:
            raise ValueError('The bot is busy; please retry shortly.')
        self.cooldowns[user] = now
        if len(self.cooldowns) > 5000:
            self.cooldowns = {k: v for k, v in self.cooldowns.items() if now - v < 60}

    async def ask(self, user, request_id, question, context=()):
        self.admit(user)
        self.busy += 1
        try:
            self.store.reserve(str(request_id), 'qa', .02)
            text, cost = await answer(self.http_session, self.api_key, self.knowledge, question, context=context)
            self.store.settle(str(request_id), cost)
            return text
        except APIError as error:
            LOG.warning('QA API failure: %s', error)
            if error.region_blocked:
                return 'QA is unavailable because this deployment cannot access OpenAI in its region. The maintainer must update the deployment.'
            return 'The QA provider is unavailable. Please contact the maintainer.'
        except TimeoutError:
            return 'Lookup timed out; please retry shortly.'
        finally:
            self.busy -= 1

    async def on_message(self, message):
        if message.author.bot or not self.eligible(message.author.id, message.guild.id if message.guild else None):
            return
        is_reply, context = await reply_context(message, self.user.id)
        if message.guild and self.user not in message.mentions and not is_reply:
            return
        question = re.sub(r'<@!?\d+>', '', message.content).strip()
        if not question:
            return
        try:
            async with message.channel.typing():
                text = await self.ask(message.author.id, message.id, question, context=context)
            await message.reply(**linked_message(text), mention_author=False)
        except ValueError as error:
            await message.reply(str(error), mention_author=False)
        except Exception as error:
            LOG.warning('QA failed: %s', type(error).__name__)
            await message.reply('Lookup unavailable; please retry later.', mention_author=False)

    async def sync_knowledge(self):
        while True:
            try:
                await self.knowledge.refresh(self.github)
                LOG.info('Knowledge refreshed: %s', self.knowledge.sha)
            except Exception as error:
                LOG.warning('Knowledge refresh failed: %s', type(error).__name__)
            await asyncio.sleep(900)

    async def poll_jobs(self):
        await self.wait_until_ready()
        while True:
            for job in self.store.pending():
                try:
                    building = job['status'] == 'building'
                    run = await (self.github.run(job['run_id'], build=building) if job['run_id'] else self.github.find_run(job['id']))
                    if not run:
                        if time.time() - job['created'] > 600:
                            self.store.update(job['id'], 'dispatch_uncertain')
                        continue
                    if run['status'] != 'completed':
                        self.store.update(job['id'], 'building' if building else 'running', run['id'], run['html_url'])
                        continue
                    if not building and run['conclusion'] == 'success':
                        preview = await self.github.find_run(job['id'], build=True)
                        if preview:
                            self.store.update(job['id'], 'building', preview['id'], preview['html_url'])
                        elif time.time() - job['created'] > 1800:
                            self.store.update(job['id'], 'build_dispatch_uncertain')
                        continue
                    # Mark before sending: a Discord timeout must not cause duplicate notifications.
                    self.store.update(job['id'], 'notifying', run['id'], run['html_url'])
                    channel = self.get_channel(int(job['channel'])) or await self.fetch_channel(int(job['channel']))
                    status = run['conclusion']
                    link = run['html_url'] if building else 'Code editing did not complete; the maintainer can inspect the private worker log.'
                    text = (f'Task `{job["id"]}`: **{status}**\n{link}\n'
                            f'Branch: `{job["branch"]}`\n'
                            'Successful editor CI runs contain the tested package. '
                            'Use /revise with this task ID for another attempt.')
                    await channel.send(**linked_message(text, label="Build"))
                    self.store.update(job['id'], status)
                except Exception as error:
                    LOG.warning('Job polling failed for %s: %s', job['id'], type(error).__name__)
                    if self.store.job(job['id'])['status'] == 'notifying':
                        self.store.update(job['id'], 'notification_uncertain')
            await asyncio.sleep(20)

    async def start_job(self, interaction, request, previous=None):
        if not self.eligible(interaction.user.id, interaction.guild_id):
            raise ValueError('This server is not enabled.')
        self.admit(interaction.user.id)
        if not 10 <= len(request) <= 3500:
            raise ValueError('Please describe the request in 10–3500 characters.')
        if self.store.repair_active():
            raise ValueError('A repair is already running; please wait for its package result.')
        if self.store.recent_jobs(interaction.user.id) >= 5:
            raise ValueError('Daily development limit reached. Please try later.')
        key = str(interaction.id)
        branch = WORK_BRANCH
        if previous:
            old = self.store.job(previous)
            if not old or old['owner'] != str(interaction.user.id) or old['channel'] != str(interaction.channel_id):
                raise ValueError('Only the original requester in the original channel can revise this task.')
            latest = self.store.latest_job()
            if latest['id'] != old['id'] or old['branch'] != WORK_BRANCH:
                raise ValueError('Please continue the latest task; the shared branch has moved on.')
            request = f'Original request:\n{old["request"][:2500]}\n\nFollow-up:\n{request}'[:6000]
        self.store.reserve(key, 'dev', JOB_CAP)
        self.store.add_job(key, interaction.user.id, interaction.channel_id, branch, request)
        try:
            await self.github.dispatch(key, request, branch)
            self.store.update(key, 'queued')
        except Exception:
            # The response can be lost after GitHub accepts dispatch. Poll rather than retry.
            LOG.warning('Dispatch outcome uncertain for %s', key)
        return (f'Task `{key}` accepted. Branch `{branch}`. Budget cap: ${JOB_CAP:.2f}.\n'
                'Editing, testing and packaging run on GitHub runners. Results will be posted here.')

    def register_commands(self):
        @self.tree.command(name='ask', description='Ask a FruitsAtelier usage, feature or bug question')
        async def ask(interaction: discord.Interaction, question: str):
            if not self.eligible(interaction.user.id, interaction.guild_id):
                await interaction.response.send_message('This server is not enabled.', ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            await interaction.followup.send(**linked_message(await self.ask(interaction.user.id, interaction.id, question)))

        @self.tree.command(name='develop', description='Try a small fix or feature on the shared repair branch')
        async def develop(interaction: discord.Interaction, request: str):
            await interaction.response.defer(thinking=True)
            await interaction.followup.send(await self.start_job(interaction, request))

        @self.tree.command(name='revise', description='Continue your previous development task')
        async def revise(interaction: discord.Interaction, task_id: str, feedback: str):
            await interaction.response.defer(thinking=True)
            await interaction.followup.send(await self.start_job(interaction, feedback, task_id))

        @self.tree.command(name='template', description='Question and feature request templates')
        async def template(interaction: discord.Interaction):
            await interaction.response.send_message(TEMPLATE, ephemeral=True)

        @self.tree.command(name='task', description='Look up one of your task results')
        async def task(interaction: discord.Interaction, task_id: str):
            job = self.store.job(task_id)
            if not job or job['owner'] != str(interaction.user.id) or job['channel'] != str(interaction.channel_id):
                await interaction.response.send_message('Task not found in this channel.', ephemeral=True)
                return
            await interaction.response.send_message(**linked_message(f'{job["status"]}\n{job["url"] or "Awaiting GitHub run discovery"}', label="Build"), ephemeral=True)

        @self.tree.error
        async def error(interaction, error):
            cause = getattr(error, 'original', error)
            text = str(cause) if isinstance(cause, ValueError) else 'Request failed; please contact the maintainer.'
            LOG.warning('Command failed: %s', type(cause).__name__)
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)


def main():
    logging.basicConfig(level=logging.INFO)
    Bot().run(secret('DISCORD_TOKEN'))


if __name__ == '__main__':
    main()
