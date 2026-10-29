import aiohttp
import io
import zipfile


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

    async def find_run(self, key):
        result = await self.request('GET', f'/repos/{self.repo}/actions/workflows/discord-develop.yml/runs?event=workflow_dispatch&per_page=100')
        matches = [r for r in result['workflow_runs'] if r['display_title'] == f'FA task {key}']
        if len(matches) > 1:
            raise RuntimeError('Duplicate workflow runs require maintainer review')
        return matches[0] if matches else None

    async def run(self, run_id):
        return await self.request('GET', f'/repos/{self.repo}/actions/runs/{run_id}')

    async def screenshots(self, run_id, key):
        result = await self.request('GET', f'/repos/{self.repo}/actions/runs/{run_id}/artifacts')
        candidates = [a for a in result['artifacts'] if a['name'] == 'demo-screenshots-' + key and not a['expired']]
        if not candidates or candidates[0]['size_in_bytes'] > 20_000_000:
            return []
        artifact = candidates[0]
        async with self.session.get(f'https://api.github.com/repos/{self.repo}/actions/artifacts/{artifact["id"]}/zip',
                                    headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > 20_000_000:
                    raise ValueError('Screenshot archive too large')
        images = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for item in archive.infolist():
                if item.filename.endswith('.png') and item.file_size <= 4_000_000:
                    content = archive.read(item)
                    if content.startswith(b'\x89PNG\r\n\x1a\n'):
                        images.append((item.filename.rsplit('/', 1)[-1], content))
                    if len(images) == 2:
                        break
        return images
