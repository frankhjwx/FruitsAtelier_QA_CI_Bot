"""Edit one durable Discord status message without model calls."""
import asyncio
import json
import logging
import time
import math
from datetime import datetime

import discord
from aiohttp import web

from .channel_policy import ignored_channel
from .transfer import locate
from .task_stages import EDIT_STAGES, VALIDATION_STAGES

INTERVAL = 20
LOG = logging.getLogger('fa_bot.progress')


def progress_snapshot(job):
    stage = {'dispatching': 'Submitting the task', 'queued': 'Waiting for a CI runner',
             'cancelling': 'Cancelling the task; waiting for GitHub jobs to stop',
             'running': 'Inspecting code and preparing the change',
             'building': 'Regression checks passed; preparing your test build',
             'dispatch_uncertain': 'Waiting to confirm that CI received the task',
             'build_dispatch_uncertain': 'Waiting to confirm the test-build dispatch'}.get(job['status'], 'Checking task status')
    checks = []
    packaging = job['status']=='building'
    if job['status'] == 'running':
        try:
            folder, _ = locate(job['id'])
            edit_path=folder/'progress-edit.json'
            edit=json.loads(edit_path.read_text(encoding='utf-8')) if edit_path.exists() else {}
            candidates = sorted(folder.glob('candidate-*.json'))
            if candidates:
                attempt = candidates[-1].stem.split('-')[-1]
                stage = f'Running regression checks (attempt {attempt})'
                for platform in ('windows',):
                    path = folder / f'result-{attempt}-{platform}.json'
                    if not path.exists():
                        checks.append(f'{platform}: pending')
                        continue
                    result = json.loads(path.read_text(encoding='utf-8'))
                    status = 'passed' if result.get('passed') is True else ('skipped' if result.get('skipped') else 'not passed')
                    checks.append(f'{platform}: {status}')
                if any('not passed' in item for item in checks):
                    stage = f'Reviewing validation results (attempt {attempt})'
            if edit.get('phase') in EDIT_STAGES:
                phase=edit['phase']; attempt=edit['attempt']
                stage=EDIT_STAGES[phase]+f' (attempt {attempt}/2)'
                packaging=phase=='packaging'
                if phase=='editing':
                    checks=[]
                if phase=='waiting_validation':
                    path=folder/f'progress-validation-{attempt}.json'
                    if path.exists():
                        validation=json.loads(path.read_text(encoding='utf-8'))
                        if validation.get('phase') in VALIDATION_STAGES:
                            stage=VALIDATION_STAGES[validation['phase']]+f' (attempt {attempt}/2)'
        except (OSError, ValueError, web.HTTPException):
            pass
    return {'stage':stage,'checks':checks,'packaging':packaging}


def remaining_text(job, now, estimate=None, started=None, queued=False):
    if job['status']=='cancelling':
        return 'Cancellation is in progress; runner shutdown time is not predictable.'
    if job['status'] in ('dispatch_uncertain','build_dispatch_uncertain'):
        return 'Estimated remaining: unavailable until the workflow is confirmed.'
    historical=estimate is not None
    low,high=estimate or (5*60,20*60)
    if queued or job['status'] in ('queued','dispatching'):
        return f'Queue wait is unknown; estimated work after starting: {math.ceil(low/60)}–{math.ceil(high/60)} min (rough estimate).'
    elapsed=max(0,now-(started if started is not None else job['created']))
    low=max(0,math.ceil((low-elapsed)/60)); high=math.ceil((high-elapsed)/60)
    if high<=0:
        return 'Taking longer than the estimate; no reliable remaining time yet.'
    amount=f'{low}–{high} min' if low>0 and low!=high else (f'about {high} min' if low==high else f'up to {high} min')
    basis='recent successful runs' if historical else 'rough initial estimate'
    return f'Estimated remaining: {amount} ({basis}; retries or runner queues may add time).'


def progress_text(job, now, snapshot=None, estimate=None, started=None, queued=False):
    snapshot=progress_snapshot(job) if snapshot is None else snapshot
    minutes, seconds = divmod(max(0, int(now - job['created'])), 60)
    return (f'Task {job["id"]}\n{snapshot["stage"]}\nElapsed: {minutes}m {seconds:02d}s'
            + ('\n' + ' · '.join(snapshot['checks']) if snapshot['checks'] else '')
            + '\n'+remaining_text(job,now,estimate,started,queued)
            + f'\nLast checked: <t:{int(now)}:T> · Updates about every 20 seconds.')


def package_stage(jobs):
    steps={
        'Prepare pinned verifier and build':'Building the Windows editor',
        'Run regression suites':'Running the final Windows regression suites',
        'Build and verify experimental package':'Building and verifying your Windows test package',
        'actions/upload-artifact@v4':'Uploading your test package',
    }
    for job in jobs:
        if job['status']=='in_progress':
            for step in job.get('steps',[]):
                if step['status']=='in_progress':
                    return steps.get(step['name'],'Preparing the Windows build environment')
    if any(job['status'] in ('queued','waiting','pending') for job in jobs):
        return 'Waiting for the Windows build runner'
    return None


async def progress_content(bot, job, run=None):
    snapshot=await asyncio.to_thread(progress_snapshot,job)
    build=job['status']=='building'
    estimate=None; started=None; queued=False
    try:
        if snapshot['packaging'] and not build:
            preview=await bot.github.find_run(job['id'],build=True)
            if preview:
                run=preview; build=True
        if run:
            queued=run['status'] in ('queued','waiting','pending','requested')
            if run.get('run_started_at'):
                started=datetime.fromisoformat(run['run_started_at'].replace('Z','+00:00')).timestamp()
            if build:
                stage=package_stage(await bot.github.run_jobs(run['id'],build=True))
                if stage: snapshot['stage']=stage
        if hasattr(bot.github,'duration_range'):
            estimate=await bot.github.duration_range(build=build)
    except Exception as error:
        LOG.warning('Live CI progress unavailable: %s',type(error).__name__)
    return progress_text(job,time.time(),snapshot,estimate,started,queued)


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
    # PartialMessage.edit does not accept send-only suppress_embeds or suppress.
    edits={key:value for key,value in payload.items() if key!='suppress_embeds'}
    await message.edit(**edits, allowed_mentions=discord.AllowedMentions.none())
    return True


async def update_progress(bot, job, force=False, run=None):
    saved = bot.store.progress(job['id'])
    now = time.time()
    if not saved or (not force and now - saved['updated'] < INTERVAL):
        return
    # Throttle failed edits too; a deleted or inaccessible message must not create a new one.
    bot.store.progress_attempt(job['id'], now)
    try:
        content = await progress_content(bot,job,run)
        from .cancellation import CancelTask
        view=None if job['status']=='cancelling' else CancelTask(bot,job['id'])
        await edit_progress(bot, job, {'content': content, 'view': view, 'suppress_embeds': True})
    except Exception as error:
        LOG.warning('Progress update failed for %s: %s', job['id'], type(error).__name__)
