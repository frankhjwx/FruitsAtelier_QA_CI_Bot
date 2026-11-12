"""Durable retrieval from the latest published stable release's exact source commit."""
import asyncio
import collections
import contextlib
import datetime as dt
import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from urllib.parse import quote

import aiohttp

from .code_index import CodeIndex, FORMAT
from .config import SOURCE_REPO
from .knowledge import Knowledge

FORMAT_VERSION = 'release-v1:' + FORMAT


class RepositoryKnowledge:
    def __init__(self, path):
        self.path = Path(path)
        self.snapshot = None
        self.release = {}
        self.fingerprint = self.sha = self.updated = ''
        if self.path.exists():
            try:
                self._restore()
            except (sqlite3.Error, ValueError, KeyError, TypeError):
                logging.getLogger('fa_bot').warning('Knowledge cache unavailable; rebuilding from GitHub')

    def _restore(self):
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT format,fingerprint,updated,release,sha,chunks,code FROM snapshot').fetchone()
            if not row or row[0] != FORMAT_VERSION:
                return
            snapshot = Knowledge()
            snapshot.sha, snapshot.updated = row[4], row[2]
            snapshot.chunks = [(p, u, b, collections.Counter(c)) for p, u, b, c in json.loads(row[5])]
            snapshot.df = collections.Counter(t for *_, counts in snapshot.chunks for t in counts)
            snapshot.code_index = CodeIndex(snapshot.sha)
            snapshot.code_index.db.deserialize(row[6])
            self.snapshot = snapshot
            self.fingerprint, self.updated, self.release, self.sha = row[1], row[2], json.loads(row[3]), row[4]

    def _save(self, snapshot, release, fingerprint, updated):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshot (format TEXT, fingerprint TEXT, updated TEXT,
                    release TEXT, sha TEXT, chunks TEXT, code BLOB);
                CREATE TABLE IF NOT EXISTS changes (id INTEGER PRIMARY KEY, updated TEXT, fingerprint TEXT, summary TEXT);
            """)
            with db:
                if snapshot.sha == self.sha:
                    db.execute('UPDATE snapshot SET fingerprint=?,updated=?,release=?',
                               (fingerprint, updated, json.dumps(release)))
                else:
                    db.execute('DELETE FROM snapshot')
                    db.execute('INSERT INTO snapshot VALUES (?,?,?,?,?,?,?)',
                               (FORMAT_VERSION, fingerprint, updated, json.dumps(release), snapshot.sha,
                                json.dumps(snapshot.chunks), snapshot.code_index.db.serialize()))
                summary = {'commit': snapshot.sha, 'release': release['tag_name'],
                           'previous_commit': self.sha, 'source_changed': snapshot.sha != self.sha}
                db.execute('INSERT INTO changes(updated,fingerprint,summary) VALUES (?,?,?)',
                           (updated, fingerprint, json.dumps(summary)))

    async def _download(self, github, sha):
        async with github.session.get(f'https://api.github.com/repos/{SOURCE_REPO}/tarball/{sha}',
                                      timeout=aiohttp.ClientTimeout(total=90)) as response:
            response.raise_for_status()
            archive = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                archive.extend(chunk)
                if len(archive) > 100_000_000:
                    raise RuntimeError('Knowledge archive exceeds size limit')
        snapshot = Knowledge()
        await asyncio.to_thread(snapshot.load, bytes(archive), sha, [], persistent_cache=False)
        return snapshot

    async def refresh(self, github):
        stable = await github.request('GET', f'/repos/{SOURCE_REPO}/releases/latest')
        if stable.get('draft') or stable.get('prerelease') or not stable.get('tag_name'):
            raise ValueError('No published stable release')
        release = {k: stable.get(k) for k in ('tag_name', 'name', 'body', 'html_url', 'published_at')}
        tag = quote(release['tag_name'], safe='')
        sha = (await github.request('GET', f'/repos/{SOURCE_REPO}/commits/{tag}'))['sha']
        fingerprint = hashlib.sha256(json.dumps([FORMAT_VERSION, sha, release], sort_keys=True).encode()).hexdigest()
        if fingerprint == self.fingerprint:
            return False
        snapshot = self.snapshot if self.snapshot and self.sha == sha else await self._download(github, sha)
        updated = dt.datetime.now(dt.timezone.utc).isoformat()
        await asyncio.to_thread(self._save, snapshot, release, fingerprint, updated)
        # Answer() takes a shallow copy; publish only after the complete snapshot is durable.
        self.snapshot, self.release = snapshot, release
        self.fingerprint, self.updated, self.sha = fingerprint, updated, sha
        return True

    def _label(self, rows):
        return [{**row, 'text': f'Release: {self.release["tag_name"]}; commit: {self.sha}\n'
                 + ('Documentation is a navigation aid; verify feature claims in release source.\n'
                    if '.md' in row['source'] else '') + row['text']} for row in rows]

    def search(self, query, limit=7, prefix=''):
        if not self.snapshot:
            return []
        rows = self._label(self.snapshot.search(query, max(1, limit-1), prefix))
        release = self.release
        return ([{'source': 'Published release ' + release['tag_name'], 'url': release['html_url'],
                  'text': f'Published: {release["published_at"]}; version: {release["tag_name"]}\n'
                          + (release.get('body') or '')[:6500]}] + rows)[:limit]

    def read_evidence(self, path, start=1):
        return self._label(self.snapshot.read_evidence(path, start)) if self.snapshot else []
