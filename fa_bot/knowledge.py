"""Bounded in-memory retrieval from public, commit-pinned repository evidence."""
import asyncio
import collections
import datetime as dt
import io
import math
import re
import tarfile
import aiohttp

from .config import SOURCE_REPO


def terms(text):
    latin = re.findall(r'[a-z0-9_]{2,}', text.lower())
    chinese = re.findall(r'[\u3400-\u9fff]+', text)
    return latin + [s[i:i+2] for s in chinese for i in range(max(1, len(s)-1))]


class Knowledge:
    def __init__(self):
        self.chunks, self.df = [], collections.Counter()
        self.sha = self.updated = ''

    def load(self, archive, sha, issues):
        chunks = []
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
            for member in tar:
                if not member.isfile() or member.size > 600_000:
                    continue
                path = member.name.partition('/')[2]
                if not (path.endswith(('.md', '.cs')) and
                        (path.startswith(('docs/', 'src/', 'macOS/src/')) or path.startswith('README'))):
                    continue
                body = tar.extractfile(member).read().decode('utf-8', 'replace')
                lines = body.splitlines()
                for start in range(0, len(lines), 60):
                    content = '\n'.join(lines[start:start+80])[:6500]
                    chunks.append((path, f'https://github.com/{SOURCE_REPO}/blob/{sha}/{path}#L{start+1}', content))
        for issue in issues:
            chunks.append((f'Issue #{issue["number"]}: {issue["title"]}', issue['html_url'],
                           f'State: {issue["state"]}; updated: {issue["updated_at"]}\n{issue.get("body") or ""}'[:6500]))
        indexed = [(path, url, body, collections.Counter(terms(path + '\n' + body)))
                   for path, url, body in chunks]
        df = collections.Counter(t for *_, counts in indexed for t in counts)
        self.chunks, self.df, self.sha = indexed, df, sha
        self.updated = dt.datetime.now(dt.timezone.utc).isoformat()

    async def refresh(self, github):
        commit = await github.request('GET', f'/repos/{SOURCE_REPO}/commits/dev')
        issues = await github.request('GET', f'/repos/{SOURCE_REPO}/issues?state=all&sort=updated&per_page=100')
        sha = commit['sha']
        async with github.session.get(f'https://api.github.com/repos/{SOURCE_REPO}/tarball/{sha}',
                                      timeout=aiohttp.ClientTimeout(total=90)) as response:
            response.raise_for_status()
            archive = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                archive.extend(chunk)
                if len(archive) > 100_000_000:
                    raise RuntimeError('Knowledge archive exceeds size limit')
        await asyncio.to_thread(self.load, bytes(archive), sha, issues)

    def search(self, query, limit=7):
        query_terms = set(terms(query))
        scores = []
        for path, url, body, counts in self.chunks:
            score = sum(math.log(1 + len(self.chunks)/(1+self.df[t])) * min(counts[t], 3)
                        for t in query_terms if counts[t])
            if path.startswith(('docs/', 'README')):
                score *= 1.3
            if score:
                scores.append((score, path, url, body))
        return [{'source': p, 'url': u, 'text': b} for _, p, u, b in sorted(scores, reverse=True)[:limit]]
