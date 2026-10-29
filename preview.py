"""Keep the editing workflow occupied until its public package run finishes."""
import json
import os
import re
import time
import urllib.request

REPO = 'frankhjwx/FruitsAtelier'


def api(path, data=None):
    request = urllib.request.Request('https://api.github.com/repos/' + REPO + path,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                 'User-Agent': 'FruitsAtelier-DiscordBot', 'Content-Type': 'application/json'},
        data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response) if response.status != 204 else None


def wait_for_package(key, sha, branch):
    if not re.fullmatch(r'\d{15,22}', key) or not re.fullmatch(r'[a-f0-9]{40}', sha) or branch != 'codex-discord-bug-fix':
        raise ValueError('Invalid task identity')
    workflow = '/actions/workflows/discord-preview.yml'

    def find():
        runs = api(workflow + '/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
        matches = [r for r in runs if r['display_title'] == 'FA preview ' + key]
        if len(matches) > 1:
            raise RuntimeError('Multiple preview runs require maintainer review')
        return matches[0] if matches else None

    run = find()
    if run is None:
        api(workflow + '/dispatches', {'ref': 'main', 'inputs': {'job_id': key, 'commit': sha, 'branch': branch}})
    started = time.monotonic()
    last_status = None
    while time.monotonic() - started < 3300:
        run = api('/actions/runs/' + str(run['id'])) if run else find()
        if not run and time.monotonic() - started > 600:
            raise RuntimeError('Preview dispatch outcome unknown; no retry was sent')
        if run:
            status = run['status']
            if status != last_status:
                print('Editor package workflow:', status, flush=True)
                last_status = status
            if status == 'completed':
                print('Editor result:', run['conclusion'], run['html_url'], flush=True)
                # The Bot reports the public run's result, including test failures.
                return
        time.sleep(20)
    raise RuntimeError('Preview did not finish before the deadline')


if __name__ == '__main__':
    wait_for_package(os.environ['JOB_ID'], os.environ['SOURCE_SHA'], os.environ['TASK_BRANCH'])
