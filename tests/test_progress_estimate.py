from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock

from fa_bot.github import GitHub
from fa_bot.progress import package_stage


class EstimateTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_durations_are_cached_and_invalid_runs_excluded(self):
        github=GitHub(None,'test','bot/repo')
        start=datetime(2026,10,29,tzinfo=timezone.utc)
        runs=[{'conclusion':'success','run_started_at':start.isoformat(),
               'updated_at':(start+timedelta(seconds=seconds)).isoformat()} for seconds in (600,1200,1800)]
        runs.extend([{'conclusion':'failure'}, {'conclusion':'success','run_started_at':'invalid','updated_at':'invalid'}])
        github.request=AsyncMock(return_value={'workflow_runs':runs})
        self.assertEqual(await github.duration_range(),(600,1800))
        self.assertEqual(await github.duration_range(),(600,1800))
        github.request.assert_awaited_once()
        self.assertIn('discord-develop.yml',github.request.call_args.args[1])
        await github.duration_range(build=True)
        self.assertIn('/frankhjwx/FruitsAtelier/',github.request.call_args.args[1])
        self.assertIn('discord-preview.yml',github.request.call_args.args[1])

    async def test_insufficient_history_returns_no_measured_estimate(self):
        github=GitHub(None,'test','bot/repo')
        github.request=AsyncMock(return_value={'workflow_runs':[]})
        self.assertIsNone(await github.duration_range())

    def test_public_package_step_names_are_not_echoed_arbitrarily(self):
        jobs=[{'status':'in_progress','steps':[{'status':'in_progress','name':'Build and verify experimental package'}]}]
        self.assertIn('verifying your Windows test package',package_stage(jobs))
        jobs[0]['steps'][0]['name']='private diagnostic text'
        self.assertNotIn('private diagnostic text',package_stage(jobs))
