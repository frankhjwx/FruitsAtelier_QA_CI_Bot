import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from fa_bot.transfer import create_transfer, transfer, ci_root
from fa_bot.attachments import TTL, cleanup
from fa_bot.messages import action_message, notify_owner
from repair_ci import verified

KEY='1565198087914599999'

class TransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ATTACHMENT_DIR':str(Path(self.temp.name)/'attachments')})
        self.env.start()
        root=Path(os.environ['ATTACHMENT_DIR']); root.mkdir()
        self.created=time.time()-300
        self.source=root/f'{int(self.created*1000)}_1_2_3'; self.source.mkdir()
        (self.source/'0.osu').write_bytes(b'osu file format v14\n[General]\nMode:2\n')
        (self.source/'manifest.json').write_text(json.dumps([{'file':'0.osu','name':'original.osu'}]))
        self.token=create_transfer(1,2,KEY,[3])
        app=web.Application(); app.router.add_route('*','/ci/{key}/{item}',transfer)
        self.client=TestClient(TestServer(app)); await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close(); self.env.stop(); self.temp.cleanup()
    async def test_job_auth_and_role_boundaries(self):
        endpoint='/ci/'+KEY+'/'
        response=await self.client.get(endpoint+'manifest'); self.assertEqual(response.status,403)
        headers={'Authorization':'Bearer '+self.token}
        response=await self.client.get(endpoint+'manifest',headers=headers); manifest=await response.json()
        self.assertLessEqual(manifest['expires'],self.created+TTL)
        response=await self.client.get(endpoint+'sample-0.osu',headers=headers)
        self.assertEqual(await response.read(),(self.source/'0.osu').read_bytes())
        tester={'Authorization':'Bearer '+manifest['test_token']}
        response=await self.client.get(endpoint+'manifest',headers=tester)
        self.assertNotIn('test_token',await response.json())
        response=await self.client.put(endpoint+'candidate-1',json={},headers=tester); self.assertEqual(response.status,403)
        response=await self.client.put(endpoint+'result-1-windows',json={},headers=headers); self.assertEqual(response.status,403)
        response=await self.client.put(endpoint+'result-1-windows',json={'passed':False},headers=tester); self.assertEqual(response.status,200)
        response=await self.client.put(endpoint+'result-1-windows',json={'passed':True},headers=tester); self.assertEqual(response.status,409)
    async def test_expiry_covers_original_and_transfer_without_extension(self):
        with patch('fa_bot.transfer.time.time',return_value=self.created+TTL+1):
            response=await self.client.get('/ci/'+KEY+'/manifest',headers={'Authorization':'Bearer '+self.token})
            self.assertEqual(response.status,410)
        self.assertEqual(cleanup(ci_root(),now=self.created+TTL+1),1)
        self.assertEqual(cleanup(Path(os.environ['ATTACHMENT_DIR']),now=self.created+TTL+1),1)
    async def test_other_users_cannot_bind_samples(self):
        with self.assertRaises(ValueError): create_transfer(99,2,'1565198087914599998',[3])

class VerificationTests(unittest.TestCase):
    def test_success_needs_exact_samples_same_test_and_baseline_failure(self):
        results=[{'platform':p,'passed':True,'test_sha256':'abc','sample_hashes':['sample']} for p in ('windows','macos')]
        self.assertFalse(verified(results,'abc',['sample']))
        results[0].update(baseline={'run':{'exit':42}},fixed={'run':{'exit':0}})
        self.assertTrue(verified(results,'abc',['sample']))
        self.assertFalse(verified(results,'other',['sample']))
        results[0]['baseline']['run']['exit']=0
        self.assertFalse(verified(results,'abc',['sample']))
    def test_notifications_only_ping_requester(self):
        payload=notify_owner(action_message('Ready.'),123)
        self.assertEqual(payload['content'],'<@123> Ready.')
        mentions=payload['allowed_mentions'].to_dict()
        self.assertEqual(mentions['users'],[123])
        self.assertNotIn('everyone',mentions['parse'])
