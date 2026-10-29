import io
import json
import re
import zipfile
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

    async def dispatch(self, key, request, branch, transfer_token=""):
        await self.request('POST', f'/repos/{self.repo}/actions/workflows/discord-develop.yml/dispatches',
                           json={'ref': 'main', 'inputs': {
                               'job_id': key, 'request': request, 'branch': branch, 'transfer_token': transfer_token}})

    async def find_run(self, key, build=False, review=False):
        repo = SOURCE_REPO if build else self.repo
        workflow = 'discord-pr.yml' if review else ('discord-preview.yml' if build else 'discord-develop.yml')
        title = f'FA PR {key}' if review else (f'FA preview {key}' if build else f'FA task {key}')
        result = await self.request('GET', f'/repos/{repo}/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=100')
        matches = [r for r in result['workflow_runs'] if r['display_title'] == title]
        if len(matches) > 1:
            raise RuntimeError('Duplicate workflow runs require maintainer review')
        return matches[0] if matches else None

    async def run(self, run_id, build=False):
        repo = SOURCE_REPO if build else self.repo
        return await self.request('GET', f'/repos/{repo}/actions/runs/{run_id}')

    async def package_url(self, run_id, key):
        data = await self.request('GET', f'/repos/{SOURCE_REPO}/actions/runs/{int(run_id)}/artifacts?per_page=100')
        found = [a for a in data['artifacts']
                 if a['name'] == 'windows-experimental-' + key and not a['expired']]
        if len(found) != 1:
            raise ValueError('Test package is missing or expired')
        return f"https://github.com/{SOURCE_REPO}/actions/runs/{int(run_id)}/artifacts/{int(found[0]['id'])}"

    async def source_receipt(self, run_id, key):
        data = await self.request('GET', f'/repos/{self.repo}/actions/runs/{run_id}/artifacts')
        found = [a for a in data['artifacts'] if a['name'] == 'source-' + key and not a['expired']]
        if len(found) != 1 or found[0]['size_in_bytes'] > 16384:
            raise ValueError('Tested source receipt is missing or invalid')
        artifact_id = int(found[0]['id'])
        async with self.session.get(f'https://api.github.com/repos/{self.repo}/actions/artifacts/{artifact_id}/zip',
                                    headers=self.headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                data.extend(chunk)
                if len(data) > 16384:
                    raise ValueError('Source receipt exceeds size limit')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.getinfo('source.json').file_size > 4096:
                raise ValueError('Source receipt exceeds size limit')
            receipt = json.loads(archive.read('source.json'))
        if receipt.get('job_id') != key or not re.fullmatch(r'[a-f0-9]{40}', receipt.get('sha', '')):
            raise ValueError('Source receipt identity mismatch')
        if receipt.get('regression_verified') is not True:
            raise ValueError('Private regression verification is missing')
        return receipt['sha']

    async def dispatch_pr(self, job, sha):
        from .handoff import decode
        request, _ = decode(job['request'])
        await self.request('POST', f'/repos/{self.repo}/actions/workflows/discord-pr.yml/dispatches',
            json={'ref': 'main', 'inputs': {'job_id': job['id'], 'commit': sha,
                              'build_run': str(job['run_id']), 'request': request[:3500]}})

    async def public_pulls(self, number=None):
        path = f'/pulls/{int(number)}' if number else '/pulls?state=all&base=dev&head=frankhjwx:codex-discord-bug-fix'
        async with self.session.get(f'https://api.github.com/repos/{SOURCE_REPO}' + path,
                                    timeout=aiohttp.ClientTimeout(total=15)) as response:
            response.raise_for_status()
            return await response.json()
