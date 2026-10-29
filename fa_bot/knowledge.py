"""Bounded in-memory retrieval from public, commit-pinned repository evidence."""
import asyncio
import collections
import datetime as dt
import io
import math
import re
import os
from pathlib import Path
import sqlite3
import tarfile
import aiohttp

from .config import SOURCE_REPO
from .code_index import CodeIndex


def terms(text):
    latin = re.findall(r'[a-z0-9_]{2,}', text.lower())
    chinese = re.findall(r'[\u3400-\u9fff]+', text)
    return latin + [s[i:i+2] for s in chinese for i in range(max(1, len(s)-1))]


class Knowledge:
    def __init__(self):
        self.chunks, self.df = [], collections.Counter()
        self.sha = self.updated = ''
        self.code_index = None

    def load(self, archive, sha, issues):
        chunks = []
        cache = Path(os.environ['STATE_FILE']).parent/'code-index.sqlite3' if os.getenv('STATE_FILE') else None
        index = self.code_index if self.code_index and self.code_index.sha == sha else None
        if index is None and cache and cache.is_file():
            try:
                index = CodeIndex.restore(cache, sha)
            except (ValueError, sqlite3.Error):
                pass
        restored = index is not None
        index = index or CodeIndex(sha)
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
            for member in tar:
                if not member.isfile() or member.size > 600_000:
                    continue
                path = member.name.partition('/')[2]
                if not (path.endswith(('.md', '.cs')) and
                        (path.startswith(('docs/', 'src/', 'tests/', 'macOS/src/')) or path.startswith('README'))):
                    continue
                body = tar.extractfile(member).read().decode('utf-8', 'replace')
                if path.endswith('.cs'):
                    if not restored:
                        index.add(path, body)
                    continue
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
        if cache and not restored:
            index.save(cache)
        self.code_index = index
        self.chunks, self.df, self.sha = indexed, df, sha
        self.updated = dt.datetime.now(dt.timezone.utc).isoformat()

    async def refresh(self, github, ref='dev'):
        commit = await github.request('GET', f'/repos/{SOURCE_REPO}/commits/{ref}')
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

    def search(self, query, limit=7, prefix=""):
        if prefix and not prefix.startswith(('docs/', 'README')):
            rows = self.code_index.search(query, limit=5, budget=10000, prefix=prefix) if self.code_index else []
            return [{"source": f'{r["path"]} ({r["symbol"]})',
                     "url": f'https://github.com/{SOURCE_REPO}/blob/{r["sha"]}/{r["path"]}#L{r["start"]}',
                     "text": r["text"]} for r in rows]
        query_terms = set(terms(query))
        scores = []
        for path, url, body, counts in self.chunks:
            if prefix and not path.startswith(prefix):
                continue
            score = sum(math.log(1 + len(self.chunks)/(1+self.df[t])) * min(counts[t], 3)
                        for t in query_terms if counts[t])
            if path.startswith(('docs/', 'README')):
                score *= 1.3
            if score:
                scores.append((score, path, url, body))
        code = self.code_index.search(query, limit=min(3,limit), budget=9000) if self.code_index and not prefix else []
        results = [{'source':f'{r["path"]} ({r["symbol"]})',
                    'url':f'https://github.com/{SOURCE_REPO}/blob/{r["sha"]}/{r["path"]}#L{r["start"]}',
                    'text':r['text']} for r in code]
        docs = [{'source': p, 'url': u, 'text': b} for _, p, u, b in sorted(scores, reverse=True)]
        return (docs[:max(0,limit-len(results))] + results)[:limit]


    def read_evidence(self, path, start=1):
        """Read bounded indexed public text, without filesystem or URL access."""
        start = max(1, int(start))
        lines = {}
        if path.endswith('.cs') and self.code_index:
            rows = self.code_index.db.execute(
                'SELECT start,body FROM chunks WHERE path=? AND CAST(start AS INTEGER)<? AND CAST(end AS INTEGER)>=? ORDER BY CAST(start AS INTEGER)',
                (path,start+80,start)).fetchall()
        else:
            rows = [(int(url.rsplit('#L',1)[-1]),body) for name,url,body,_ in self.chunks
                    if name == path and '#L' in url]
        for first,body in rows:
            for number,line in enumerate(body.splitlines(),int(first)):
                if start <= number < start+80:
                    lines[number]=line
        if not lines:
            return []
        body='\n'.join(f'{number}: {line}' for number,line in sorted(lines.items()))
        return [{'source':f'{path}:{start}', 'url':f'https://github.com/{SOURCE_REPO}/blob/{self.sha}/{path}#L{start}',
                 'text':body[:7000]}]
