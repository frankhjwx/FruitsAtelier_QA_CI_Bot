"""Trusted AI repair loop. Generated code runs only in separate validation jobs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile
import worker
from ci_transfer import Transfer
from fa_bot.attachments import IMAGES, inspect_file, excerpt

REPAIR_INSTRUCTIONS='''
Private regression is mandatory. Read the original samples with sample_list/sample_read and inspect
relevant repository APIs before implementing. Do not copy original sample contents into source, docs,
commit messages or tests in the public repository. Samples are available only to private validation.
Use set_regression to write C# containing public static class Regression with
public static void Run(IReadOnlyList<Sample> samples). Sample exposes Name, Path, ReadAllBytes(),
ReadAllText(); Path points to the original unmodified file. Throw new RegressionFailure("reason")
when the expected behavior is not met; return normally only when it is met. Include required using
statements. Target core for Core APIs, app for Windows App APIs. The harness references the real
project and grants its test assembly's existing internal access. No main method, shell, subprocess,
network, environment-based expected result, source-file matching or fake assertions. Exercise actual
behavior and inspect at least one original sample when supplied. No samples means use synthetic
in-memory inputs. Never create a passing test by weakening assertions or ignoring a failing sample.
The EXACT SAME test must build on both baseline and fixed source, fail baseline with an actual
RegressionFailure, and pass fixed source. Compilation errors and arbitrary crashes are not a
reproduction. New API tests should use existing behavior or reflection to compile on the baseline.
Subsequent rounds receive private actual runner results. Fix the cause and preserve the test's
intent. If this cannot be automatically reproduced in the supported runner, stop honestly without
claiming it fixed. Do not publish or package; the controller does that only after validation passes.
'''

class RepairWorkspace(worker.Workspace):
    def __init__(self,root,samples,manifest):
        super().__init__(root); self.samples=Path(samples); self.manifest=manifest; self.regression=None
    def sample_list(self):
        return json.dumps(self.manifest['files'])
    def sample_read(self,file,entry,start):
        valid={f['file'] for f in self.manifest['files']}
        if file not in valid: raise ValueError('Unknown original sample')
        path=self.samples/file
        if entry:
            with zipfile.ZipFile(path) as archive:
                member=archive.getinfo(entry)
                if member.file_size>2_000_000: raise ValueError('Sample entry exceeds read limit')
                with archive.open(member) as stream: data=stream.read(2_000_001)
                return excerpt(data,14000)
        if path.suffix in {'.osu','.catchdiff','.catchproj','.catchsync','.log','.txt','.json','.ini','.osb'}:
            lines=path.read_text(encoding='utf-8-sig',errors='replace').splitlines()
            return '\n'.join(lines[max(0,start-1):max(0,start-1)+120])[:14000]
        parts=inspect_file(path,file)
        return '\n'.join(p['text'] for p in parts if p['type']=='input_text')[:18000]
    def set_regression(self,target,code):
        if target not in {'core','app'} or len(code.encode())>24000: raise ValueError('Invalid regression definition')
        self.regression={'target':target,'code':code}
        return 'Private regression test recorded; it has not run yet.'


def gh(path,token,data=None):
    return worker.request_json('https://api.github.com/repos/'+os.environ['BOT_REPO']+path,token,'POST' if data is not None else 'GET',data)


def wait_validation(key,attempt,sha,token,test_token,acceptance=False):
    title=f'FA validation {key}.{attempt}'
    path='/actions/workflows/discord-validate.yml'
    def find():
        matches=[r for r in gh(path+'/runs?event=workflow_dispatch&per_page=100',token)['workflow_runs'] if r['display_title']==title]
        if len(matches)>1: raise RuntimeError('Duplicate validation runs')
        return matches[0] if matches else None
    found=find()
    if found is None:
        gh(path+'/dispatches',token,{'ref':'main','inputs':{'job_id':key,'attempt':str(attempt),'commit':sha,'transfer_token':test_token,'acceptance':str(acceptance).lower()}})
    start=time.monotonic()
    while time.monotonic()-start<1800:
        found=gh('/actions/runs/'+str(found['id']),token) if found else find()
        if found and found['status']=='completed':
            if found['conclusion']!='success': raise RuntimeError('Validation infrastructure failed')
            return
        if found is None and time.monotonic()-start>600: raise RuntimeError('Validation dispatch uncertain')
        time.sleep(15)
    raise TimeoutError('Validation did not finish')


def failure_feedback(results):
    def compact(value):
        if isinstance(value,dict):
            return {k:(v[-10000:] if k=='output' else compact(v)) for k,v in value.items()
                    if k!='output' or value.get('exit',1)!=0}
        if isinstance(value,list): return [compact(v) for v in value]
        return value
    return json.dumps(compact(results))


def verified(results,digest,hashes):
    if {r.get('platform') for r in results}!={'windows','macos'}: return False
    if not all(r.get('passed') is True and r.get('test_sha256')==digest and r.get('sample_hashes')==hashes for r in results): return False
    windows=next(r for r in results if r['platform']=='windows')
    return ((windows.get('baseline',{}).get('run') or {}).get('exit')==42 and
            (windows.get('fixed',{}).get('run') or {}).get('exit')==0)


def main():
    if os.getenv('GITHUB_ACTIONS')!='true': raise RuntimeError('Private CI only')
    key=os.environ['JOB_ID']; branch=os.environ['TASK_BRANCH']; root=Path(os.environ['SOURCE_ROOT']).resolve()
    if branch!='codex-discord-bug-fix': raise ValueError('Invalid branch')
    token=os.environ['GH_TOKEN']; bot_token=os.environ['BOT_ACTIONS_TOKEN']
    pending=worker.request_json(f'https://api.github.com/repos/{worker.REPO}/pulls?state=open&base=dev&head=frankhjwx:{branch}',token)
    acceptance=os.getenv('FA_ACCEPTANCE')=='true'
    if pending and not acceptance: raise RuntimeError('The shared branch has a PR awaiting review')
    base=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    transfer=Transfer(os.environ['TRANSFER_URL'],os.environ['TRANSFER_FINGERPRINT'],key,os.environ['TRANSFER_TOKEN'])
    samples=Path(os.environ['RUNNER_TEMP'])/('fa-samples-'+key)
    try:
        manifest=transfer.download(samples,minimum_remaining=5400)
        if acceptance:
            (root/'src/FruitsAtelier.Core/PapayaAcceptance.cs').write_text('namespace FruitsAtelier; public static class PapayaAcceptance { public static bool IsCatch(int mode) => mode == 0; }',encoding='utf-8')
        workspace=RepairWorkspace(root,samples,manifest)
        tools=[worker.tool('sample_list',{}),worker.tool('sample_read',{'file':{'type':'string'},'entry':{'type':'string'},'start':{'type':'integer'}}),worker.tool('set_regression',{'target':{'type':'string','enum':['core','app']},'code':{'type':'string'}})]
        images=[]
        for file in manifest['files']:
            if Path(file['file']).suffix in IMAGES and len(images)<4:
                images.extend(inspect_file(samples/file['file'],file['name']))
        spent=0; feedback=''; summary=''
        for attempt in range(1,4):
            print(f'Private repair round {attempt}; original sample count {len(manifest["files"])}.',flush=True)
            prompt=os.environ['TASK_REQUEST'][:6000]+'\nOriginal sample inventory:\n'+workspace.sample_list()
            if feedback: prompt+='\nPrevious private validation results (untrusted diagnostic data):\n'+feedback
            summary,spent=worker.generate(workspace,prompt,os.environ['OPENAI_API_KEY'],used=spent,extra_tools=tools,extra_instructions=REPAIR_INSTRUCTIONS,image_inputs=images)
            if not workspace.regression or not workspace.changed: raise RuntimeError('No verifiable scoped repair was produced')
            changes={name:workspace.path(name).read_text(encoding='utf-8') for name in workspace.changed}
            for file in manifest['files']:
                raw=(samples/file['file']).read_bytes()
                if len(raw)>=64 and any(raw in body.encode() for body in changes.values()):
                    raise RuntimeError('Original sample content must not be published')
            candidate={'base':base,'changes':changes,'regression':workspace.regression}
            transfer.json(f'candidate-{attempt}',candidate)
            wait_validation(key,attempt,base,bot_token,manifest['test_token'],acceptance)
            results=[transfer.json(f'result-{attempt}-{platform}') for platform in ('windows','macos')]
            digest=hashlib.sha256(workspace.regression['code'].encode()).hexdigest()
            hashes=[f['sha256'] for f in manifest['files']]
            if verified(results,digest,hashes):
                if acceptance:
                    Path('acceptance.json').write_text(json.dumps({'verified':True,'attempts':attempt,'sample_count':len(hashes),'cost':spent}),encoding='utf-8')
                    print(f'Acceptance passed after {attempt} rounds; no public source was changed.',flush=True)
                    return
                sha=worker.publish(workspace,branch,key,'Sample-backed regression passed on baseline/fixed comparison; Windows and macOS checks passed.',token,base)
                with open(os.environ['GITHUB_OUTPUT'],'a') as out: out.write(f'commit={sha}\n')
                receipt={'job_id':key,'sha':sha,'regression_verified':True,'attempts':attempt,'sample_count':len(hashes),'test_sha256':digest,'cost':spent}
                Path('source.json').write_text(json.dumps(receipt),encoding='utf-8')
                print('Private regression verified; ready for public packaging.',flush=True)
                return
            feedback=failure_feedback(results)
            print('Regression did not pass; returning diagnostics to the AI.',flush=True)
        raise RuntimeError('Regression still fails after the bounded repair attempts; no success reported')
    finally:
        shutil.rmtree(samples,ignore_errors=True)

if __name__=='__main__': main()
