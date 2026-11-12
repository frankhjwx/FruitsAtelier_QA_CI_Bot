import collections
import contextlib
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fa_bot.code_index import CodeIndex
from fa_bot.knowledge import Knowledge, terms
from fa_bot.repository_knowledge import RepositoryKnowledge
from fa_bot.qa_usage import usage_cost


def branch(sha):
    result = Knowledge()
    result.sha = sha
    body = 'Preview supports scrolling ' + sha
    result.chunks = [('docs/Preview.md', f'https://github.com/frankhjwx/FruitsAtelier/blob/{sha}/docs/Preview.md#L1',
                      body, collections.Counter(terms(body))) ]
    result.df = collections.Counter(terms(body))
    result.code_index = CodeIndex(sha)
    result.code_index.add('src/Preview.cs', 'class Preview { public void Scroll() {} }')
    return result


class RepositoryKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'knowledge.sqlite3'
        self.commit = 'a'*40
        self.release = {'tag_name': 'v1.4.3', 'body': 'Preview scrolling released', 'name': '1.4.3',
                        'html_url': 'https://github.com/frankhjwx/FruitsAtelier/releases/tag/v1.4.3',
                        'published_at': '2026-11-12'}
        async def request(method, path):
            if '/commits/' in path:
                return {'sha': self.commit}
            if '/issues?' in path:
                return []
            return self.release
        self.github = SimpleNamespace(request=AsyncMock(side_effect=request))
        self.knowledge = RepositoryKnowledge(self.path)
        self.knowledge._download = AsyncMock(side_effect=lambda github, sha: branch(sha))

    async def test_no_change_skips_download_and_disk_writes_and_restart_restores(self):
        self.assertTrue(await self.knowledge.refresh(self.github))
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        self.assertFalse(await self.knowledge.refresh(self.github))
        self.assertEqual(self.knowledge._download.await_count, 1)
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))
        restored = RepositoryKnowledge(self.path)
        self.assertEqual(restored.search('Preview'), self.knowledge.search('Preview'))
        self.assertIn('Scroll', restored.read_evidence('src/Preview.cs')[0]['text'])
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM changes').fetchone()[0], 1)

    async def test_new_release_rebuilds_source_and_old_answer_keeps_snapshot(self):
        await self.knowledge.refresh(self.github)
        old = copy.copy(self.knowledge)
        self.commit = 'c'*40
        self.release = {**self.release, 'tag_name': 'v1.4.4'}
        await self.knowledge.refresh(self.github)
        self.assertEqual(self.knowledge._download.await_count, 2)
        self.assertEqual(old.sha, 'a'*40)
        self.assertEqual(old.snapshot.sha, old.sha)
        self.assertEqual(RepositoryKnowledge(self.path).sha, 'c'*40)
        rows = self.knowledge.search('Preview')
        self.assertEqual(rows[0]['source'], 'Published release v1.4.4')
        self.assertIn('/' + 'c'*40 + '/', self.knowledge.read_evidence('docs/Preview.md')[0]['url'])
        paths = [call.args[1] for call in self.github.request.await_args_list]
        self.assertFalse(any('/dev' in path or '/main' in path or '/issues' in path for path in paths))
        self.assertTrue(any('/commits/v1.4.4' in path for path in paths))

    async def test_metadata_update_does_not_rebuild_source(self):
        await self.knowledge.refresh(self.github)
        self.release = {**self.release, 'body': 'Corrected release note'}
        await self.knowledge.refresh(self.github)
        self.assertEqual(self.knowledge._download.await_count, 1)
        restored = RepositoryKnowledge(self.path)
        self.assertIn('Corrected release note', restored.search('Preview')[0]['text'])
        self.assertTrue(restored.search('Scroll', prefix='src/'))

    async def test_failure_keeps_complete_snapshot_on_disk_and_in_memory(self):
        await self.knowledge.refresh(self.github)
        before = self.path.read_bytes()
        self.commit = 'c'*40
        self.knowledge._download.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            await self.knowledge.refresh(self.github)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.knowledge.snapshot.sha, 'a'*40)
        self.assertEqual(self.knowledge.sha, 'a'*40)


class SolUsageTests(unittest.TestCase):
    def test_usage_accounts_for_cache_reads_and_both_write_lifetimes(self):
        self.assertAlmostEqual(usage_cost({'input_tokens': 10000, 'output_tokens': 1000,
            'input_tokens_details': {'cached_tokens': 6000, 'cache_write_tokens': 2000,
                'cache_write_tokens_details': {'ttl_30m_tokens': 1000, 'ttl_12h_tokens': 1000}}}), .0221)
        self.assertAlmostEqual(usage_cost({'input_tokens': 10000, 'output_tokens': 1000}), .03)
