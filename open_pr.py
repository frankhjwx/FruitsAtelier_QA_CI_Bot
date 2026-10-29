"""Open a dev PR for a user-confirmed package; never merge or enable auto-merge."""
import os
import re
from worker import request_json, REPO

BRANCH = 'codex-discord-bug-fix'


def propose(key, sha, build_id, request, token):
    if not re.fullmatch(r'\d{15,22}', key) or not re.fullmatch(r'[a-f0-9]{40}', sha) or not build_id.isdigit():
        raise ValueError('Invalid tested task identity')
    def gh(path, method='GET', data=None):
        return request_json(f'https://api.github.com/repos/{REPO}/' + path, token, method, data)
    run = gh('actions/runs/' + build_id)
    if run['status'] != 'completed' or run['conclusion'] != 'success' or run['display_title'] != 'FA preview ' + key:
        raise ValueError('The package run did not pass for this task')
    ref = gh('git/ref/heads/' + BRANCH)
    if ref['object']['sha'] != sha:
        raise ValueError('Shared branch has changed since this package was built')
    existing = gh('pulls?state=open&base=dev&head=frankhjwx:' + BRANCH)
    if existing:
        if len(existing) != 1 or existing[0]['head']['sha'] != sha:
            raise ValueError('An existing PR requires maintainer review')
        return existing[0]
    body = ('The requester confirmed that the experimental package resolves the request.\n\n'
            'Request:\n' + request[:3500] + '\n\nTested commit: `' + sha + '`\n'
            'Package CI: ' + run['html_url'] + '\n\n'
            'Target: dev. Maintainer review and merge are required.')
    return gh('pulls', 'POST', {'base': 'dev', 'head': BRANCH,
              'title': 'Discord tested change ' + key, 'body': body, 'draft': False})


if __name__ == '__main__':
    if os.getenv('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('PR creation runs only on GitHub Actions')
    result = propose(os.environ['JOB_ID'], os.environ['SOURCE_SHA'], os.environ['BUILD_RUN'],
                     os.environ['TASK_REQUEST'], os.environ['GH_TOKEN'])
    print('PR opened:', result['html_url'])
