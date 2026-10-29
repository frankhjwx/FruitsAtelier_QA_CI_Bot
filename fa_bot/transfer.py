"""Short-lived, job-scoped transfer for private CI; no raw GitHub artifacts."""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import tempfile
import time
from aiohttp import web
from .attachments import TTL, MAX_BATCH, root_path, cleanup


CANDIDATE_LIMIT = 8_000_000


def ci_root():
    return root_path().parent / 'ci-transfers'


def create_transfer(owner, channel, key, attachment_ids=()):
    root = ci_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cleanup(root)
    sources = []
    created = time.time()
    for mid in list(dict.fromkeys(attachment_ids))[:3]:
        folders = sorted(root_path().glob(f'*_{int(owner)}_{int(channel)}_{int(mid)}'))
        valid = [f for f in folders if not f.is_symlink() and time.time()-int(f.name.split('_')[0])/1000 < TTL]
        if not valid:
            raise ValueError('An attached sample has expired. Please upload it again before requesting a repair.')
        folder = valid[-1]
        created = min(created, int(folder.name.split('_')[0])/1000)
        for record in json.loads((folder/'manifest.json').read_text(encoding='utf-8')):
            path = folder/record['file']
            if path.parent != folder or path.is_symlink() or not path.is_file():
                raise ValueError('An attached sample is unavailable. Please upload it again.')
            sources.append((path, record['name']))
    if created + TTL - time.time() < 7200:
        raise ValueError('These attachments expire too soon for CI. Please upload fresh copies.')
    if sum(p.stat().st_size for p, _ in sources) > MAX_BATCH:
        raise ValueError('Please narrow the reproduction samples to 40 MiB total.')
    if sum(p.stat().st_size for p in root.glob('*/*') if p.is_file()) + MAX_BATCH > 500*1024*1024:
        raise ValueError('CI attachment storage is full. Please try again later.')
    folder = root/f'{int(created*1000)}_{int(owner)}_{int(channel)}_{int(key)}'
    folder.mkdir(mode=0o700)
    edit, test = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    files=[]
    try:
        for i,(source,name) in enumerate(sources):
            name_on_disk = f'sample-{i}{source.suffix}'
            shutil.copyfile(source, folder/name_on_disk)
            os.chmod(folder/name_on_disk,0o600)
            files.append({'file':name_on_disk,'name':name,'sha256':hashlib.sha256((folder/name_on_disk).read_bytes()).hexdigest(),'size':source.stat().st_size})
        access={'job':str(key),'expires':created+TTL,'edit':edit,'test':test,'files':files}
        (folder/'access.json').write_text(json.dumps(access),encoding='utf-8')
        os.chmod(folder/'access.json',0o600)
    except BaseException:
        shutil.rmtree(folder)
        raise
    return edit


def locate(key):
    if not re.fullmatch(r'\d{15,22}',key):
        raise web.HTTPNotFound()
    matches=list(ci_root().glob('*_'+key))
    if len(matches)!=1 or matches[0].is_symlink():
        raise web.HTTPNotFound()
    folder=matches[0]
    data=json.loads((folder/'access.json').read_text(encoding='utf-8'))
    if time.time()>=data['expires']:
        raise web.HTTPGone()
    return folder,data


async def transfer(request):
    key=request.match_info['key']; item=request.match_info['item']
    folder,data=locate(key)
    token=request.headers.get('Authorization','').removeprefix('Bearer ')
    role=next((role for role in ('edit','test') if hmac.compare_digest(token,data[role])),None)
    if role is None:
        raise web.HTTPForbidden()
    if request.method=='GET' and item=='manifest':
        result={k:data[k] for k in ('job','expires','files')}
        if role=='edit': result['test_token']=data['test']
        return web.json_response(result,headers={'Cache-Control':'no-store'})
    if request.method=='GET' and item in {f['file'] for f in data['files']}:
        return web.Response(body=await asyncio.to_thread((folder/item).read_bytes),headers={'Cache-Control':'no-store'})
    if not re.fullmatch(r'(candidate-[1-3]|diagnostic-[1-3]|result-[1-3]-(windows|macos))',item):
        raise web.HTTPNotFound()
    if item.startswith('diagnostic') and role!='edit':
        raise web.HTTPForbidden()
    if request.method=='PUT':
        if (item.startswith('candidate') and role!='edit') or (item.startswith('result') and role!='test'):
            raise web.HTTPForbidden()
        body=await request.read()
        limit = CANDIDATE_LIMIT if item.startswith('candidate-') else 1_000_000
        if len(body)>limit: raise web.HTTPRequestEntityTooLarge(max_size=limit,actual_size=len(body))
        json.loads(body)
        await asyncio.to_thread(store_immutable, folder/(item+'.json'), body)
        return web.json_response({'stored':True})
    if request.method=='GET':
        path=folder/(item+'.json')
        if not path.is_file(): raise web.HTTPNotFound()
        return web.Response(body=await asyncio.to_thread(path.read_bytes),content_type='application/json',headers={'Cache-Control':'no-store'})
    raise web.HTTPMethodNotAllowed(request.method,['GET','PUT'])


def store_immutable(path, body):
    # Publish a complete file atomically; retrying a lost acknowledgement cannot overwrite it.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            output.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != body:
                raise web.HTTPConflict()
    finally:
        temp.unlink(missing_ok=True)


async def start_transfer_server():
    cert=os.getenv('CI_TRANSFER_CERT')
    if not cert: return None
    app=web.Application(client_max_size=CANDIDATE_LIMIT)
    app.router.add_route('*','/ci/{key}/{item}',transfer)
    runner=web.AppRunner(app,access_log=None)
    await runner.setup()
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert,os.environ['CI_TRANSFER_KEY'])
    await web.TCPSite(runner,'0.0.0.0',8443,ssl_context=tls).start()
    return runner
