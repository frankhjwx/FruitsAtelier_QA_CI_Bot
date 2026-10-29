import aiohttp
from .config import SOURCE_REPO


class GitHub:
    def __init__(self, session, token, repo):
        self.session, self.repo = session, repo
        self.headers = {'Authorization': f'Bearer {token}',
                        'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28'}

    async def request(self, method, path, **kwargs):
        async with self.session.request(method, 'https://api.github.com' + path,
                                        headers=self.headers,
                                        timeout=aiohttp.ClientTimeout(total=15), **kwargs) as response:
            if response.status >= 400:
                # API payloads can contain private request text; log only the status.
                raise RuntimeError(f'GitHub returned HTTP {response.status}')
            return await response.json() if response.status != 204 else None

    async def dispatch(self, key, request, branch):
        await self.request('POST', f'/repos/{self.repo}/actions/workflows/discord-develop.yml/dispatches',
                           json={'ref': 'main', 'inputs': {
                               'job_id': key, 'request': request, 'branch': branch}})

    async def find_run(self, key, build=False):
        repo = SOURCE_REPO if build else self.repo
        workflow = 'discord-preview.yml' if build else 'discord-develop.yml'
        title = f'FA preview {key}' if build else f'FA task {key}'
        result = await self.request('GET', f'/repos/{repo}/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=100')
        matches = [r for r in result['workflow_runs'] if r['display_title'] == title]
        if len(matches) > 1:
            raise RuntimeError('Duplicate workflow runs require maintainer review')
        return matches[0] if matches else None

    async def run(self, run_id, build=False):
        repo = SOURCE_REPO if build else self.repo
        return await self.request('GET', f'/repos/{repo}/actions/runs/{run_id}')
