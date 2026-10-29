import asyncio
import contextlib
import logging
import os
import re
import time

import aiohttp
import discord
from discord import app_commands

from .channel_policy import ignored_channel, ignore_interaction
from .conversation import reply_context
from .attachments import AttachmentStore
from .transfer import create_transfer, start_transfer_server
from .config import GUILDS, OWNER, JOB_CAP, WORK_BRANCH, secret
from .github import GitHub
from .knowledge import Knowledge
from .messages import linked_message, qa_message, action_message, notify_owner
from .qa import APIError, QAReply, answer
from .handoff import encode, decode, ready
from .store import Store
from .reviews import PackageReview, poll_reviews
from .progress import bind_progress, update_progress, edit_progress

LOG = logging.getLogger('fa_bot')
TEMPLATE = ('Usage: version, OS, goal, what you tried.\n'
            'Bug: version, steps, expected vs actual, sanitized example.\n'
            'Feature: use case, desired behavior, one concrete example.')


class CommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        return not await ignore_interaction(interaction)


class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = CommandTree(self)
        self.store = Store(os.getenv('STATE_FILE', 'state/bot.sqlite3'))
        self.knowledge = Knowledge()
        self.attachments = AttachmentStore()
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
        self.transfer_server = await start_transfer_server()
        latest = self.store.latest_job()
        if latest and latest['status'] == 'success' and self.store.review(latest['id']):
            self.add_view(PackageReview(self, latest['id']))
        self.workers = [asyncio.create_task(self.sync_knowledge()), asyncio.create_task(self.poll_jobs()),
                        asyncio.create_task(poll_reviews(self))]

    async def close(self):
        if getattr(self, 'transfer_server', None):
            await self.transfer_server.cleanup()
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

    def admit(self, user, kind="qa"):
        user = (user, kind)
        now = time.monotonic()
        if now - self.cooldowns.get(user, -100) < 10:
            raise ValueError('Please wait 10 seconds before another request.')
        if self.busy >= 3:
            raise ValueError('The bot is busy; please retry shortly.')
        self.cooldowns[user] = now
        if len(self.cooldowns) > 5000:
            self.cooldowns = {k: v for k, v in self.cooldowns.items() if now - v < 60}

    async def ask(self, user, request_id, question, context=(), uploads=(), channel=None, preflight=False):
        if not preflight:
            self.admit(user)
        elif self.busy >= 3:
            raise ValueError("The bot is busy; please retry shortly.")
        self.busy += 1
        try:
            self.store.reserve(str(request_id), 'qa', .02, owner=user)
            parts = []
            ids = [item['attachment_id'] for item in reversed(context) if 'attachment_id' in item]
            if uploads:
                await self.attachments.capture(self.http_session, user, channel, request_id, uploads)
                ids.insert(0, request_id)
                ids = self.attachments.available_ids(user, channel, ids)
            if ids:
                parts = await asyncio.to_thread(self.attachments.read, user, channel, ids)
            text, cost = await answer(self.http_session, self.api_key, self.knowledge, question, context=context, attachments=parts)
            self.store.settle(str(request_id), cost)
            return QAReply(text.text, text.change_request, tuple(ids[:3]), text.localization)
        except APIError as error:
            LOG.warning('QA API failure: %s', error)
            if error.region_blocked:
                return QAReply('QA is unavailable because this deployment cannot access OpenAI in its region. The maintainer must update the deployment.')
            return QAReply('The QA provider is unavailable. Please contact the maintainer.')
        except TimeoutError:
            return QAReply('Lookup timed out; please retry shortly.')
        finally:
            self.busy -= 1

    async def on_message(self, message):
        if ignored_channel(message.channel):
            return
        if message.author.bot or not self.eligible(message.author.id, message.guild.id if message.guild else None):
            return
        is_reply, context = await reply_context(message, self.user.id)
        if message.guild and self.user not in message.mentions and not is_reply:
            return
        question = re.sub(r'<@!?\d+>', '', message.content).strip()
        uploads = getattr(message, 'attachments', ())
        if not question and not uploads:
            return
        question = question or 'Please inspect the attached files and explain any relevant issues.'
        try:
            async with message.channel.typing():
                text = await self.ask(message.author.id, message.id, question, context=context, uploads=uploads, channel=message.channel.id)
            payload = qa_message(text, self, message.author.id, message.channel.id)
            sent = await message.reply(**payload, mention_author=False)
            if text.change_request:
                payload['view'].message = sent
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
                        await update_progress(self, self.store.job(job['id']))
                        continue
                    if run['status'] != 'completed':
                        phase = 'building' if building else ('running' if run['status'] == 'in_progress' else 'queued')
                        self.store.update(job['id'], phase, run['id'], run['html_url'])
                        await update_progress(self, self.store.job(job['id']))
                        continue
                    if not building and run['conclusion'] == 'success':
                        if not self.store.review(job['id']):
                            sha = await self.github.source_receipt(run['id'], job['id'])
                            self.store.save_source(job['id'], sha)
                        preview = await self.github.find_run(job['id'], build=True)
                        if preview:
                            self.store.update(job['id'], 'building', preview['id'], preview['html_url'])
                        elif time.time() - job['created'] > 1800:
                            self.store.update(job['id'], 'build_dispatch_uncertain')
                        await update_progress(self, self.store.job(job['id']))
                        continue
                    status = run['conclusion']
                    package = await self.github.package_url(run['id'], job['id']) if building and status == 'success' else None
                    # Mark before sending: a Discord timeout must not cause duplicate notifications.
                    self.store.update(job['id'], 'notifying', run['id'], run['html_url'])
                    channel = self.get_channel(int(job['channel'])) or await self.fetch_channel(int(job['channel']))
                    if package:
                        text = ('Your test build is ready! The targeted regression and automated checks passed. Download it below and try your change. '
                                'GitHub sign-in is required.\n\n'
                                'If it meets your requirements, click the confirmation button to create a PR for maintainer review. '
                                'It will not merge automatically. '
                                'If anything needs adjusting, use /revise and describe what to change.')
                        payload = action_message(text, 'Download test build', package)
                        if self.store.review(job['id']):
                            payload['view'] = PackageReview(self, job['id'], payload['view'])
                    else:
                        text = ("I couldn't finish preparing your test build. " if building else "I couldn't complete the requested changes. ")
                        payload = action_message(text + 'The repair has not been verified. The maintainer can check the failure or budget limit. Use /revise with more details to try again.')
                    if not await edit_progress(self, job, payload):
                        if not ignored_channel(channel):
                            await channel.send(**notify_owner(payload, job['owner']))
                    self.store.update(job['id'], status)
                except Exception as error:
                    LOG.warning('Job polling failed for %s: %s', job['id'], type(error).__name__)
                    if self.store.job(job['id'])['status'] == 'notifying':
                        self.store.update(job['id'], 'notification_uncertain')
            await asyncio.sleep(20)

    async def start_job(self, interaction, request, previous=None, attachment_ids=(), localization=None):
        if not self.eligible(interaction.user.id, interaction.guild_id):
            raise ValueError('This server is not enabled.')
        self.admit(interaction.user.id, "dev")
        request = request.strip()
        minimum = 1 if previous else 10
        if not minimum <= len(request) <= 3500:
            raise ValueError(f'Please describe the request in {minimum}-3500 characters.')
        if self.store.repair_active():
            raise ValueError('A repair or PR is already running; please wait for its package or maintainer review.')
        if self.store.recent_jobs(interaction.user.id) >= 100:
            raise ValueError('Daily development limit reached. Please try later.')
        key = str(interaction.id)
        branch = WORK_BRANCH
        if previous:
            old = self.store.job(previous)
            if not old or old['owner'] != str(interaction.user.id) or old['channel'] != str(interaction.channel_id):
                raise ValueError('Only the original requester in the original channel can revise this task.')
            latest = self.store.latest_job()
            if latest['id'] != old['id']:
                raise ValueError('This is not the latest task. Use /revise without task_id to continue the latest task.')
            if old['branch'] != WORK_BRANCH:
                raise ValueError('This task belongs to a retired repair branch. Please submit a new request.')
            original, localization = decode(old['request'])
            request = f'Original request:\n{original[:2500]}\n\nFollow-up:\n{request}'[:6000]
        if previous and not attachment_ids:
            attachment_ids = self.store.job_attachments(previous)
        if previous or not ready(localization) or localization.get('commit') != self.knowledge.sha:
            context = [{'role':'user', 'content':'Use the attached evidence for this confirmed development request.',
                        'attachment_id': item} for item in attachment_ids]
            reply = await self.ask(interaction.user.id, 'preflight-'+key, request,
                                   context=context, channel=interaction.channel_id, preflight=True)
            if not reply.change_request or not ready(reply.localization):
                return reply.text + '\n\nCI has not started: QA must locate relevant implementation code first.'
            localization = reply.localization
        request = encode(request, localization)
        transfer_token = await asyncio.to_thread(create_transfer, interaction.user.id, interaction.channel_id, key, attachment_ids)
        # QA and attachment transfer yield; another requester may have claimed the shared branch.
        if self.store.repair_active():
            raise ValueError('A repair or PR is already running; please wait for its package or maintainer review.')
        if previous and self.store.latest_job()['id'] != previous:
            raise ValueError('This is not the latest task. Use /revise to continue the latest task.')
        self.store.reserve(key, 'dev', JOB_CAP)
        self.store.add_job(key, interaction.user.id, interaction.channel_id, branch, request)
        self.store.save_attachments(key, attachment_ids)
        try:
            await self.github.dispatch(key, request, branch, transfer_token=transfer_token)
            self.store.update(key, 'queued')
        except Exception:
            # The response can be lost after GitHub accepts dispatch. Poll rather than retry.
            LOG.warning('Dispatch outcome uncertain for %s', key)
        return ('Your request has been accepted. I will try the changes and run the checks. '
                'I will post the test build here when it is ready, or let you know if something goes wrong.')


    def register_commands(self):
        @self.tree.command(name='ask', description='Ask a FruitsAtelier usage, feature or bug question')
        async def ask(interaction: discord.Interaction, question: str, attachment: discord.Attachment | None = None):
            if not self.eligible(interaction.user.id, interaction.guild_id):
                await interaction.response.send_message('This server is not enabled.', ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            reply = await self.ask(interaction.user.id, interaction.id, question, uploads=[attachment] if attachment else (), channel=interaction.channel_id)
            payload = qa_message(reply, self, interaction.user.id, interaction.channel_id)
            sent = await interaction.followup.send(**payload, wait=True)
            if reply.change_request:
                payload['view'].message = sent

        @self.tree.command(name='develop', description='Try a small fix or feature on the shared repair branch')
        async def develop(interaction: discord.Interaction, request: str):
            await interaction.response.defer(thinking=True)
            sent = await interaction.followup.send(await self.start_job(interaction, request), wait=True)
            await bind_progress(self, str(interaction.id), sent)

        @self.tree.command(name='revise', description='Continue your previous development task')
        async def revise(interaction: discord.Interaction, feedback: str, task_id: str | None = None, attachment: discord.Attachment | None = None):
            await interaction.response.defer(thinking=True)
            if task_id is None:
                latest = self.store.latest_job()
                if not latest:
                    raise ValueError('No development task is available to revise.')
                task_id = latest['id']
            if attachment:
                if not self.eligible(interaction.user.id, interaction.guild_id):
                    raise ValueError('This server is not enabled.')
                await self.attachments.capture(self.http_session, interaction.user.id, interaction.channel_id, interaction.id, [attachment])
                status = await self.start_job(interaction, feedback, task_id.strip(), attachment_ids=(interaction.id,))
            else:
                status = await self.start_job(interaction, feedback, task_id.strip())
            sent = await interaction.followup.send(status, wait=True)
            await bind_progress(self, str(interaction.id), sent)

        @self.tree.command(name='template', description='Question and feature request templates')
        async def template(interaction: discord.Interaction):
            await interaction.response.send_message(TEMPLATE, ephemeral=True)

        @self.tree.command(name='task', description='Look up one of your task results')
        async def task(interaction: discord.Interaction, task_id: str):
            job = self.store.job(task_id)
            if not job or job['owner'] != str(interaction.user.id) or job['channel'] != str(interaction.channel_id):
                await interaction.response.send_message('Task not found in this channel.', ephemeral=True)
                return
            if job['status'] == 'success':
                await interaction.response.defer(ephemeral=True)
                try:
                    url = await self.github.package_url(job['run_id'], job['id'])
                    payload = action_message('Your test build is ready. GitHub sign-in is required to download it.', 'Download test build', url)
                except ValueError:
                    payload = action_message('This test build is no longer available. Please ask the maintainer for a new build.')
                await interaction.followup.send(**payload, ephemeral=True)
            else:
                await interaction.response.send_message(f'Task status: {job["status"]}', ephemeral=True)

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
