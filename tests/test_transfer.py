import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from fa_bot.transfer import create_transfer, transfer, ci_root, CANDIDATE_LIMIT
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
        app=web.Application(client_max_size=CANDIDATE_LIMIT); app.router.add_route('*','/ci/{key}/{item}',transfer)
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
        response=await self.client.put(endpoint+'result-1-windows',json={'passed':False},headers=tester); self.assertEqual(response.status,200)
        response=await self.client.put(endpoint+'result-1-windows',json={'passed':True},headers=tester); self.assertEqual(response.status,409)
    async def test_candidate_resources_fit_without_expanding_diagnostic_limit(self):
        endpoint='/ci/'+KEY+'/'
        headers={'Authorization':'Bearer '+self.token}
        body={'changes':{'src/FruitsAtelier.Core/Localization/en.json':'x'*1_100_000}}
        response=await self.client.put(endpoint+'candidate-1',json=body,headers=headers)
        self.assertEqual(response.status,200)
        response=await self.client.put(endpoint+'diagnostic-1',json=body,headers=headers)
        self.assertEqual(response.status,413)
        response=await self.client.put(endpoint+'candidate-2',json={'data':'x'*CANDIDATE_LIMIT},headers=headers)
        self.assertEqual(response.status,413)

    async def test_progress_is_role_scoped_bounded_replaceable_and_expires(self):
        endpoint='/ci/'+KEY+'/'
        edit={'Authorization':'Bearer '+self.token}
        manifest=await (await self.client.get(endpoint+'manifest',headers=edit)).json()
        test={'Authorization':'Bearer '+manifest['test_token']}
        payload={'phase':'editing','attempt':1}
        self.assertEqual((await self.client.put(endpoint+'progress-edit',json=payload,headers=test)).status,403)
        self.assertEqual((await self.client.put(endpoint+'progress-edit',json=payload,headers=edit)).status,200)
        before=await (await self.client.get(endpoint+'progress-edit',headers=edit)).json()
        await self.client.put(endpoint+'progress-edit',json=payload,headers=edit)
        after=await (await self.client.get(endpoint+'progress-edit',headers=edit)).json()
        self.assertEqual(before['started'],after['started'])
        self.assertGreaterEqual(after['updated'],before['updated'])
        payload={'phase':'waiting_validation','attempt':2}
        self.assertEqual((await self.client.put(endpoint+'progress-edit',json=payload,headers=edit)).status,200)
        self.assertEqual((await (await self.client.get(endpoint+'progress-edit',headers=edit)).json())['attempt'],2)
        self.assertEqual((await self.client.put(endpoint+'progress-edit',json={**payload,'private':'original sample'},headers=edit)).status,400)
        self.assertEqual((await self.client.put(endpoint+'progress-edit',json={'phase':'sample content','attempt':1},headers=edit)).status,400)
        self.assertEqual((await self.client.put(endpoint+'progress-validation-2',json={'phase':'fixed','attempt':2},headers=edit)).status,403)
        self.assertEqual((await self.client.put(endpoint+'progress-validation-2',json={'phase':'fixed','attempt':1},headers=test)).status,400)
        self.assertEqual((await self.client.put(endpoint+'progress-validation-2',json={'phase':'fixed','attempt':2},headers=test)).status,200)
        with patch('fa_bot.transfer.time.time',return_value=self.created+TTL+1):
            self.assertEqual((await self.client.get(endpoint+'progress-edit',headers=edit)).status,410)

    async def test_cancellation_revokes_edit_and_validation_capabilities(self):
        from fa_bot.transfer import revoke_transfer
        endpoint='/ci/'+KEY+'/'
        edit={'Authorization':'Bearer '+self.token}
        manifest=await (await self.client.get(endpoint+'manifest',headers=edit)).json()
        test={'Authorization':'Bearer '+manifest['test_token']}
        revoke_transfer(KEY)
        for headers in (edit,test):
            self.assertEqual((await self.client.get(endpoint+'manifest',headers=headers)).status,410)
        self.assertEqual((await self.client.put(endpoint+'candidate-1',json={},headers=edit)).status,410)
        self.assertEqual((await self.client.put(endpoint+'result-1-windows',json={},headers=test)).status,410)

    async def test_expiry_covers_original_and_transfer_without_extension(self):
        with patch('fa_bot.transfer.time.time',return_value=self.created+TTL+1):
            response=await self.client.get('/ci/'+KEY+'/manifest',headers={'Authorization':'Bearer '+self.token})
            self.assertEqual(response.status,410)
        self.assertEqual(cleanup(ci_root(),now=self.created+TTL+1),1)
        self.assertEqual(cleanup(Path(os.environ['ATTACHMENT_DIR']),now=self.created+TTL+1),1)
    async def test_other_users_cannot_bind_samples(self):
        with self.assertRaises(ValueError): create_transfer(99,2,'1565198087914599998',[3])

    async def test_resume_is_owner_scoped_immutable_and_keeps_original_expiry(self):
        from fa_bot.transfer import locate
        headers={'Authorization':'Bearer '+self.token}
        candidate={'base':'a'*40,'changes':{'src/Test.cs':'class Test {}'},'regression':{'target':'core','code':'test'}}
        await self.client.put('/ci/'+KEY+'/candidate-1',json=candidate,headers=headers)
        new='1565198087914599998'
        with self.assertRaises(ValueError):create_transfer(99,2,new,resume_job=KEY)
        with self.assertRaises(ValueError):create_transfer(1,3,new,resume_job=KEY)
        with self.assertRaises(ValueError):create_transfer(1,2,new,resume_job=KEY)
        token=create_transfer(1,2,new,[3],resume_job=KEY)
        manifest=await (await self.client.get('/ci/'+new+'/manifest',headers={'Authorization':'Bearer '+token})).json()
        self.assertLessEqual(manifest['expires'],self.created+TTL)
        self.assertEqual(manifest['resume']['source_job'],KEY)
        response=await self.client.get('/ci/'+new+'/resume',headers={'Authorization':'Bearer '+token})
        self.assertEqual(await response.json(),candidate)
        test={'Authorization':'Bearer '+manifest['test_token']}
        self.assertEqual((await self.client.get('/ci/'+new+'/resume',headers=test)).status,403)
        self.assertNotIn('resume',await (await self.client.get('/ci/'+new+'/manifest',headers=test)).json())
        self.assertEqual(locate(new)[0].joinpath('resume.json').read_bytes(),locate(KEY)[0].joinpath('candidate-1.json').read_bytes())

    async def test_final_diagnostics_are_private_immutable_and_expire(self):
        headers={'Authorization':'Bearer '+self.token}
        endpoint='/ci/'+KEY+'/'
        manifest=await (await self.client.get(endpoint+'manifest',headers=headers)).json()
        tester={'Authorization':'Bearer '+manifest['test_token']}
        response=await self.client.put(endpoint+'diagnostic-1',json={'final_text':'Concrete blocker'},headers=headers)
        self.assertEqual(response.status,200)
        self.assertEqual((await self.client.get(endpoint+'diagnostic-1',headers=tester)).status,403)
        self.assertEqual((await self.client.put(endpoint+'diagnostic-1',json={},headers=headers)).status,409)
        self.assertEqual((await (await self.client.get(endpoint+'diagnostic-1',headers=headers)).json())['final_text'],'Concrete blocker')
        with patch('fa_bot.transfer.time.time',return_value=self.created+TTL+1):
            self.assertEqual((await self.client.get(endpoint+'diagnostic-1',headers=headers)).status,410)

class VerificationTests(unittest.TestCase):
    def test_infrastructure_failure_does_not_rerun(self):
        from repair_ci import wait_validation
        run={'id':10,'status':'completed','conclusion':'failure','run_attempt':1,'display_title':f'FA validation {KEY}.1'}
        with patch('repair_ci.gh',side_effect=[{'workflow_runs':[run]},run]) as api:
            with self.assertRaisesRegex(RuntimeError,'automatic reruns are disabled'):
                wait_validation(KEY,1,'a'*40,'fake','test')
        self.assertFalse(any(len(c.args)>2 for c in api.call_args_list))

    def test_third_candidate_rejected_before_dispatch(self):
        from repair_ci import wait_validation
        with patch('repair_ci.gh') as api:
            with self.assertRaisesRegex(ValueError,'two candidate'):
                wait_validation(KEY,3,'a'*40,'fake','test')
        api.assert_not_called()

    def test_success_needs_exact_samples_same_test_and_baseline_failure(self):
        results=[{'platform':p,'passed':True,'test_sha256':'abc','sample_hashes':['sample']} for p in ('windows',)]
        self.assertFalse(verified(results,'abc',['sample']))
        results[0].update(baseline={'build':{'exit':0},'run':{'exit':42}},fixed={'build':{'exit':0},'run':{'exit':0}})
        self.assertTrue(verified(results,'abc',['sample']))
        self.assertFalse(verified([{**results[0], 'platform':'macos'}],'abc',['sample']))
        self.assertFalse(verified(results,'other',['sample']))
        results[0]['baseline']['run']['exit']=0
        self.assertFalse(verified(results,'abc',['sample']))
    def test_notifications_only_ping_requester(self):
        payload=notify_owner(action_message('Ready.'),123)
        self.assertEqual(payload['content'],'<@123> Ready.')
        mentions=payload['allowed_mentions'].to_dict()
        self.assertEqual(mentions['users'],[123])
        self.assertNotIn('everyone',mentions['parse'])


class DispatchResponseTests(unittest.TestCase):
    def test_no_content_dispatch_response_is_success(self):
        import worker
        from unittest.mock import MagicMock
        response=MagicMock(); response.status=204
        response.__enter__.return_value=response
        with patch.object(worker.urllib.request,'urlopen',return_value=response):
            self.assertIsNone(worker.request_json('https://api.github.com/example','fake','POST',{}))


class DiagnosticTests(unittest.TestCase):
    def test_early_failure_is_not_lost_in_successful_log_tail(self):
        from validate_private import diagnostic_output
        output=diagnostic_output(b'FAIL original sample assertion\n' + b'PASS unrelated check\n'*2000)
        self.assertIn('FAIL original sample assertion',output)
        self.assertLess(len(output),14500)


class TransferRetryTests(unittest.TestCase):
    def test_progress_failure_is_short_and_never_retries_the_task(self):
        from ci_transfer import Transfer
        client=object.__new__(Transfer)
        with patch.object(client,'_request_once',side_effect=TimeoutError()) as request:
            client.progress('edit','editing',1)
        request.assert_called_once_with('progress-edit',{'phase':'editing','attempt':1},timeout=5)

    def test_transient_reads_and_identical_writes_retry(self):
        from ci_transfer import Transfer
        client=object.__new__(Transfer)
        with patch.object(client,'_request_once',side_effect=[TimeoutError(),b'{}']) as request, patch('ci_transfer.time.sleep'):
            self.assertEqual(client.request('candidate-1'),b'{}')
            self.assertEqual(request.call_count,2)
        with patch.object(client,'_request_once',side_effect=TimeoutError()) as request, patch('ci_transfer.time.sleep'):
            with self.assertRaises(TimeoutError): client.request('candidate-1')
            self.assertEqual(request.call_count,4)
        with patch.object(client,'_request_once',side_effect=TimeoutError()) as request, patch('ci_transfer.time.sleep'):
            with self.assertRaises(TimeoutError): client.request('result-1-windows',{'passed':False})
            self.assertEqual(request.call_count,4)
        with patch.object(client,'_request_once',side_effect=[TimeoutError(),b'{"stored":true}']) as request, patch('ci_transfer.time.sleep'):
            self.assertEqual(client.json('result-1-windows',{'passed':False}),{'stored':True})
            self.assertEqual(request.call_args_list[0],request.call_args_list[1])
        with patch.object(client,'_request_once',side_effect=ValueError('Transfer TLS fingerprint mismatch')) as request:
            with self.assertRaises(ValueError): client.request('candidate-1')
            self.assertEqual(request.call_count,1)

    def test_only_transient_http_statuses_retry(self):
        from ci_transfer import Transfer, TransferHTTPError
        client=object.__new__(Transfer)
        for status, count in ((503,4),(401,1),(403,1),(404,1),(409,1),(413,1)):
            with self.subTest(status=status), patch.object(client,'_request_once',side_effect=TransferHTTPError(status)) as request, patch('ci_transfer.time.sleep'):
                with self.assertRaises(TransferHTTPError):client.request('result-1-windows',{'passed':False})
                self.assertEqual(request.call_count,count)

    def test_event_capability_is_task_scoped_without_step_environment(self):
        from ci_transfer import token_from_environment
        with tempfile.TemporaryDirectory() as folder:
            event=Path(folder)/'event.json'
            event.write_text(json.dumps({'inputs':{'job_id':KEY,'transfer_token':'test-only-capability'}}))
            with patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(event),'JOB_ID':KEY},clear=True):
                self.assertEqual(token_from_environment(),'test-only-capability')
                os.environ['JOB_ID']='other'
                with self.assertRaises(ValueError):token_from_environment()

    def test_utf8_event_with_chinese_request_under_windows_default_encoding(self):
        from ci_transfer import token_from_environment
        original_read_text = Path.read_text

        def windows_read_text(path, encoding=None, errors=None):
            return original_read_text(path, encoding=encoding or 'cp1252', errors=errors)

        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder)/'event.json'
            event.write_text(json.dumps({'inputs':{
                'job_id':KEY, 'transfer_token':'test-only-capability',
                'request':'新增一个编辑器预设主题，重启后保留',
            }}, ensure_ascii=False), encoding='utf-8')
            with patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(event),'JOB_ID':KEY},clear=True), \
                    patch.object(Path,'read_text',windows_read_text):
                with self.assertRaises(UnicodeDecodeError):event.read_text()
                self.assertEqual(token_from_environment(),'test-only-capability')
                os.environ['JOB_ID']='other'
                with self.assertRaises(ValueError):token_from_environment()

    def test_atomic_identical_retry_preserves_content_and_conflicts(self):
        from concurrent.futures import ThreadPoolExecutor
        from fa_bot.transfer import store_immutable
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'result-1-windows.json'
            body=b'{"passed":false}'
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _:store_immutable(path,body),range(12)))
            self.assertEqual(path.read_bytes(),body)
            with self.assertRaises(web.HTTPConflict):store_immutable(path,b'{"passed":true}')
            self.assertEqual(list(Path(folder).iterdir()),[path])
