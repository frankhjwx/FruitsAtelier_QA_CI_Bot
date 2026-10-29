"""Edit one durable Discord status message without model calls."""
import asyncio
import json
import logging
import time

import discord
from aiohttp import web

from .channel_policy import ignored_channel
from .transfer import locate

INTERVAL = 120
LOG = logging.getLogger('fa_bot.progress')


def progress_text(job, now):
    stage = {'dispatching': 'Submitting the task', 'queued': 'Waiting for a CI runner',
             'running': 'Inspecting code and preparing the change',
             'building': 'Regression checks passed; preparing your test build',
             'dispatch_uncertain': 'Waiting to confirm that CI received the task',
             'build_dispatch_uncertain': 'Waiting to confirm the test-build dispatch'}.get(job['status'], 'Checking task status')
    checks = []
    if job['status'] == 'running':
        try:
            folder, _ = locate(job['id'])
            candidates = sorted(folder.glob('candidate-*.json'))
            if candidates:
                attempt = candidates[-1].stem.split('-')[-1]
                stage = f'Running regression checks (attempt {attempt})'
                for platform in ('windows', 'macos'):
                    path = folder / f'result-{attempt}-{platform}.json'
                    if not path.exists():
                        checks.append(f'{platform}: pending')
                        continue
                    result = json.loads(path.read_text())
                    status = 'passed' if result.get('passed') is True else ('skipped' if result.get('skipped') else 'not passed')
                    checks.append(f'{platform}: {status}')
                if any('not passed' in item for item in checks):
                    stage = f'Reviewing validation results (attempt {attempt})'
        except (OSError, ValueError, web.HTTPException):
            pass
    minutes, seconds = divmod(max(0, int(now - job['created'])), 60)
    return (f'Task {job["id"]}\n{stage}\nElapsed: {minutes}m {seconds:02d}s'
            + ('\n' + ' · '.join(checks) if checks else '')
            + f'\nLast checked: <t:{int(now)}:T> · Updates about every 2 minutes.')


async def bind_progress(bot, key, message):
    job = bot.store.job(key)
    if not job:
        return False
    bot.store.bind_progress(key, message.id)
    await update_progress(bot, job, force=True)
    return True


async def edit_progress(bot, job, payload):
    saved = bot.store.progress(job['id'])
    if not saved:
        return False
    channel = bot.get_channel(int(job['channel'])) or await bot.fetch_channel(int(job['channel']))
    if ignored_channel(channel):
        return True
    message = channel.get_partial_message(int(saved['message_id']))
    await message.edit(**payload, allowed_mentions=discord.AllowedMentions.none())
    return True


async def update_progress(bot, job, force=False):
    saved = bot.store.progress(job['id'])
    now = time.time()
    if not saved or (not force and now - saved['updated'] < INTERVAL):
        return
    # Throttle failed edits too; a deleted or inaccessible message must not create a new one.
    bot.store.progress_attempt(job['id'], now)
    try:
        content = await asyncio.to_thread(progress_text, job, now)
        await edit_progress(bot, job, {'content': content, 'view': None, 'suppress_embeds': True})
    except Exception as error:
        LOG.warning('Progress update failed for %s: %s', job['id'], type(error).__name__)
