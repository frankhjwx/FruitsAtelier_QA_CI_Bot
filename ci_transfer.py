"""Pinned TLS client for private CI transfer; never print capabilities or samples."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urlsplit


class TransferHTTPError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'Transfer returned HTTP {status}')


def token_from_environment():
    token = os.environ.get('TRANSFER_TOKEN')
    if not token:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        inputs = event['inputs']
        if inputs['job_id'] != os.environ['JOB_ID']:
            raise ValueError('Transfer task identity mismatch')
        token = inputs['transfer_token']
    return token

class Transfer:
    def __init__(self,url,fingerprint,key,token):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.path not in ('','/') or not re.fullmatch(r'\d{15,22}',key):
            raise ValueError('Invalid transfer endpoint')
        self.host,self.port=parsed.hostname,parsed.port or 443
        self.fingerprint,self.key,self.token=fingerprint,key,token

    def request(self,item,data=None):
        attempts = 4
        for attempt in range(attempts):
            try:
                return self._request_once(item,data)
            except (OSError, http.client.HTTPException, TransferHTTPError) as error:
                if isinstance(error, TransferHTTPError) and error.status not in (408, 429, 500, 502, 503, 504):
                    raise
                if attempt + 1 == attempts:
                    raise
                print(f'Private transfer retry {attempt + 1}/{attempts - 1}: {type(error).__name__}', flush=True)
                time.sleep(2 ** attempt)

    def _request_once(self,item,data=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',item): raise ValueError('Invalid transfer resource')
        connection=http.client.HTTPSConnection(self.host,self.port,context=ssl._create_unverified_context(),timeout=30)
        try:
            connection.connect()
            actual=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if actual!=self.fingerprint: raise ValueError('Transfer TLS fingerprint mismatch')
            body=json.dumps(data).encode() if data is not None else None
            connection.request('PUT' if data is not None else 'GET',f'/ci/{self.key}/{item}',body,
                               {'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            response=connection.getresponse()
            if response.status!=200: raise TransferHTTPError(response.status)
            raw=response.read(40*1024*1024+1)
            if len(raw)>40*1024*1024: raise ValueError('Transfer exceeds size limit')
            return raw
        finally:
            connection.close()

    def json(self,item,data=None):
        return json.loads(self.request(item,data))

    def download(self,root,minimum_remaining=1800):
        manifest=self.json('manifest')
        if manifest['expires']-time.time()<minimum_remaining: raise ValueError('Samples expire too soon; upload fresh samples')
        root=Path(root); root.mkdir(parents=True,exist_ok=True)
        total=0
        for entry in manifest['files']:
            name=entry['file']
            if not re.fullmatch(r'sample-\d+\.[a-z0-9]+',name): raise ValueError('Invalid sample name')
            data=self.request(name); total+=len(data)
            if total>40*1024*1024 or len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
                raise ValueError('Sample integrity mismatch')
            (root/name).write_bytes(data)
        return manifest
