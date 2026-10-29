"""Bounded QA localization carried as untrusted task data to the CI worker."""
import json
import re
from urllib.parse import unquote, urlsplit
from .config import SOURCE_REPO


def locator(query, direction, sources, evidence, sha):
    selected = []
    for item in reversed(evidence):
        url = urlsplit(item.get('url', ''))
        prefix = f'/{SOURCE_REPO}/blob/{sha}/'
        if url.hostname != 'github.com' or not url.path.startswith(prefix):
            continue
        path = unquote(url.path[len(prefix):])
        if not path.startswith(('src/', 'tests/', 'macOS/', 'docs/')) or '..' in path.split('/'):
            continue
        line = re.fullmatch(r'L(\d+)(?:-L\d+)?', url.fragment)
        number = int(line[1]) if line else 1
        if not any(value in sources for value in (item.get('source'), item.get('url'), path)):
            observed = {int(n) for n in re.findall(r'^(\d+):', item.get('text', ''), re.M)}
            matches = [re.fullmatch(re.escape(path)+r':(?:L)?(\d+)', source) for source in sources]
            references = [int(match[1]) for match in matches if match and int(match[1]) in observed]
            if not references:
                continue
            number = references[0]
        selected.append({'path': path[:240], 'line': number,
                         'source': item['source'][:300]})
    selected.sort(key=lambda item: (not item['source'].startswith(item['path']+':'), item['path'].startswith('docs/')))
    unique = {}
    for item in selected:
        unique.setdefault(item['path'], item)
    return {'query': str(query)[:200], 'direction': str(direction)[:400],
            'commit': sha, 'locations': list(unique.values())[:6]}


def encode(request, localization=None):
    if not localization:
        return request
    return json.dumps({'qa_handoff': 1, 'request': request, 'localization': localization}, ensure_ascii=False)


def decode(value):
    try:
        data = json.loads(value)
        if data.get('qa_handoff') == 1 and isinstance(data.get('request'), str) and isinstance(data.get('localization'), dict):
            return data['request'], data['localization']
    except (ValueError, AttributeError):
        pass
    return value, None


def ready(localization):
    """A documentation-only lookup cannot authorize a paid repair."""
    return (isinstance(localization, dict)
            and bool(localization.get('query'))
            and bool(localization.get('direction'))
            and bool(re.fullmatch(r'[a-f0-9]{40}', str(localization.get('commit', ''))))
            and any(isinstance(item, dict) and isinstance(item.get('path'), str)
                    and item['path'].startswith(('src/', 'macOS/src/'))
                    and item['path'].endswith('.cs') and '..' not in item['path'].split('/')
                    for item in localization.get('locations', [])))
