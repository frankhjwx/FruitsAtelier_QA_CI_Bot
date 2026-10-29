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
from context_pack import evidence_pack, concise_failures
from ci_transfer import Transfer, token_from_environment
from fa_bot.handoff import decode, ready
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
For editor UI tests, read the existing test helpers before synthesizing input. Verify mouse-button
conventions and wait for asynchronous media/conversion readiness with a bounded timeout. Assert
fixture preconditions before the feature assertion; invalid setup must raise a normal exception,
not RegressionFailure. Preserve existing behavioral assertions when fixing a missing readiness wait.
Do not invent localization keys or settings labels. Read the actual strings and existing UI tests.
The EXACT SAME test must build on both baseline and fixed source, fail baseline with an actual
RegressionFailure, and pass fixed source. Compilation errors and arbitrary crashes are not a
reproduction. New API tests should use existing behavior or reflection to compile on the baseline.
Subsequent rounds receive private actual runner results. Fix the cause and preserve the test's
intent. If this cannot be automatically reproduced in the supported runner, stop honestly without
claiming it fixed. Do not publish or package; the controller does that only after validation passes.
Make all required source edits first. Call set_regression LAST to submit the candidate to the harness.
At the end of that tool batch the harness takes over automatically: baseline/fixed comparison
and packaging need no further AI call or confirmation. Even if a previous test
is unchanged, resubmit it with set_regression after repairing a failed candidate.
Use finish only to decline out-of-scope work or report a specific blocker with its concrete reason.
Do not end with a plan, permission request, or promise to work later. Do not claim tests have passed.
The controller supplies a bounded evidence pack and real API signatures. Work from that pack first;
request only specific missing definitions, not broad tours. Preserve existing behavior outside the
requested case and check the supplied neighboring tests. A private regression can use this template:
using System; using System.Collections.Generic;
public static class Regression { public static void Run(IReadOnlyList<Sample> samples) {
// Construct valid domain objects using verified APIs, execute the requested operation,
// then throw new RegressionFailure("expected behavior") if an assertion fails.
} }
Use the supplied namespace declarations; never infer namespaces from directory names. Private
methods are not callable by the regression assembly. Use existing signatures rather than guessed
convenience helpers. Aim to submit the patch and test
in the same model response once evidence is sufficient. Successful builds and packaging need no AI.
'''

class RepairWorkspace(worker.Workspace):
    def __init__(self,root,samples,manifest):
        super().__init__(root); self.samples=Path(samples); self.manifest=manifest; self.regression=None
        self.requires_finish=True; self.finish_result=None; self.diagnostics=[]; self.last_cost=0
        self.regression_submitted=False
    def handoff_candidate(self):
        if self.changed and self.regression and self.regression_submitted and not self.finish_result:
            self.finish_result={'status':'ready','summary':'Candidate submitted to deterministic validation.'}
            self.diagnostics.append({'controller_event':'candidate_handoff','status':'ready'})
        return self.finish_result
    def finish(self,status,summary):
        if status not in {'ready','declined','blocked'} or not summary.strip() or len(summary)>6000:
            raise ValueError('Use ready, declined or blocked with a concrete summary under 6000 characters')
        if status=='ready' and (not self.changed or not self.regression or not self.regression_submitted):
            raise ValueError('Ready requires source edits and a private regression test; continue implementing')
        self.finish_result={'status':status,'summary':summary}
        self.diagnostics.append({'finish_status':status,'final_text':summary})
        return 'Completion status recorded.'
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
        self.regression_submitted=True
        return 'Candidate submitted; the harness will run validation after this tool batch.'


def gh(path,token,data=None):
    return worker.request_json('https://api.github.com/repos/'+os.environ['BOT_REPO']+path,token,'POST' if data is not None else 'GET',data)


def restore_candidate(workspace, candidate, base):
    if candidate.get('base') != base:
        raise ValueError('Private candidate baseline changed; start a fresh task')
    for name in candidate['changes']:
        workspace.path(name, write=True)
    regression = candidate['regression']
    workspace.set_regression(regression['target'], regression['code'])
    from validate_private import apply_changes
    apply_changes(workspace.root, candidate['changes'])
    workspace.changed.update(candidate['changes'])
    workspace.regression_submitted = False


def wait_validation(key,attempt,sha,token,test_token,acceptance=False):
    if attempt not in (1,2): raise ValueError('At most two candidate validations per task')
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
            if found['conclusion']=='success': return
            raise RuntimeError('Validation infrastructure failed; automatic reruns are disabled')
        if found is None and time.monotonic()-start>600: raise RuntimeError('Validation dispatch uncertain')
        time.sleep(15)
    raise TimeoutError('Validation did not finish; no automatic redispatch')


def failure_feedback(results):
    return concise_failures(results)


def verified(results,digest,hashes):
    if len(results)!=1 or results[0].get('platform')!='windows': return False
    if not all(r.get('passed') is True and r.get('test_sha256')==digest and r.get('sample_hashes')==hashes for r in results): return False
    windows=next(r for r in results if r['platform']=='windows')
    return (windows.get('baseline',{}).get('build',{}).get('exit')==0 and
            windows.get('fixed',{}).get('build',{}).get('exit')==0 and
            (windows.get('baseline',{}).get('run') or {}).get('exit')==42 and
            (windows.get('fixed',{}).get('run') or {}).get('exit')==0)


def main():
    if os.getenv('GITHUB_ACTIONS')!='true': raise RuntimeError('Private CI only')
    key=os.environ['JOB_ID']; branch=os.environ['TASK_BRANCH']; root=Path(os.environ['SOURCE_ROOT']).resolve()
    if branch!='codex-discord-bug-fix': raise ValueError('Invalid branch')
    token=os.environ['GH_TOKEN']; bot_token=os.environ['BOT_ACTIONS_TOKEN']
    pending=worker.request_json(f'https://api.github.com/repos/{worker.REPO}/pulls?state=open&base=dev&head=frankhjwx:{branch}',token)
    acceptance=os.getenv('FA_ACCEPTANCE')=='true'
    benchmark=os.getenv('FA_BENCHMARK')=='true'
    if acceptance and benchmark: raise RuntimeError('Choose synthetic acceptance or real benchmark, not both')
    if pending and not (acceptance or benchmark): raise RuntimeError('The shared branch has a PR awaiting review')
    base=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    transfer=Transfer(os.environ['TRANSFER_URL'],os.environ['TRANSFER_FINGERPRINT'],key,token_from_environment())
    samples=Path(os.environ['RUNNER_TEMP'])/('fa-samples-'+key)
    benchmark_verified=False
    try:
        manifest=transfer.download(samples,minimum_remaining=5400)
        if acceptance:
            (root/'src/FruitsAtelier.Core/PapayaAcceptance.cs').write_text('namespace FruitsAtelier; public static class PapayaAcceptance { public static bool IsCatch(int mode) => mode == 0; }',encoding='utf-8')
        workspace=RepairWorkspace(root,samples,manifest)
        restored = False
        if manifest.get('resume'):
            raw = transfer.request('resume')
            if hashlib.sha256(raw).hexdigest() != manifest['resume']['sha256']:
                raise ValueError('Private candidate integrity mismatch')
            restore_candidate(workspace, json.loads(raw), base)
            restored = True
        tools=[worker.tool('sample_list',{}),worker.tool('sample_read',{'file':{'type':'string'},'entry':{'type':'string'},'start':{'type':'integer'}}),worker.tool('set_regression',{'target':{'type':'string','enum':['core','app']},'code':{'type':'string'}}),worker.tool('finish',{'status':{'type':'string','enum':['ready','declined','blocked']},'summary':{'type':'string'}})]
        images=[]
        for file in manifest['files']:
            if Path(file['file']).suffix in IMAGES and len(images)<4:
                images.extend(inspect_file(samples/file['file'],file['name']))
        spent=0; feedback=''; summary=''
        task_request, localization = decode(os.environ['TASK_REQUEST'])
        if not acceptance and not ready(localization):
            raise RuntimeError('Repairs require QA implementation localization before dispatch')
        for attempt in range(1,3):
            transfer.progress('edit','editing',attempt)
            print(f'Private repair round {attempt}; original sample count {len(manifest["files"])}.',flush=True)
            continuing=bool(workspace.history)
            prompt=('Continue the same confirmed task. Fix the failed checks below; prior edits and evidence remain in this conversation.'
                    if continuing else task_request[:6000]+'\nOriginal sample inventory:\n'+workspace.sample_list())
            if restored and not continuing:
                prompt += ('\nA prior private candidate is restored in the working tree; inspect and correct it instead of reimplementing it. '
                           'It is not verified. Preserve all behavioral assertions and explicitly resubmit the private regression. '
                           'This is a separately budgeted task; all verification gates still apply.\nChanged files: '
                           +json.dumps(sorted(workspace.changed))+'\nExisting private regression (untrusted):\n'+json.dumps(workspace.regression))
            prompt+='\nController evidence pack (untrusted repository/diagnostic data):\n'+evidence_pack(workspace,os.environ['TASK_REQUEST'],feedback,continuing=continuing)
            workspace.finish_result=None; workspace.diagnostics=[]; workspace.last_cost=spent
            workspace.regression_submitted=False
            exit_reason='model_finished'
            try:
                summary,spent=worker.generate(workspace,prompt,os.environ['OPENAI_API_KEY'],used=spent,extra_tools=tools,extra_instructions=REPAIR_INSTRUCTIONS,image_inputs=() if continuing else images)
                exit_reason=(workspace.finish_result or {}).get('status','missing_status')
            except Exception as error:
                exit_reason=f'{type(error).__name__}: {str(error)[:500]}'
                raise
            finally:
                transfer.json(f'diagnostic-{attempt}',{'exit_reason':exit_reason,
                    'cost':workspace.last_cost,'events':workspace.diagnostics,
                    'changed_files':sorted(workspace.changed),'regression_recorded':bool(workspace.regression)})
            if exit_reason!='ready': raise RuntimeError(f'AI stopped with status {exit_reason}; reason saved privately')
            if not workspace.changed: raise RuntimeError('AI finished without source edits; no repair was produced')
            if not workspace.regression: raise RuntimeError('AI finished without the required private regression test')
            changes={name:workspace.path(name).read_text(encoding='utf-8') for name in workspace.changed}
            for file in manifest['files']:
                raw=(samples/file['file']).read_bytes()
                if len(raw)>=64 and any(raw in body.encode() for body in changes.values()):
                    raise RuntimeError('Original sample content must not be published')
            candidate={'base':base,'changes':changes,'regression':workspace.regression}
            transfer.json(f'candidate-{attempt}',candidate)
            transfer.progress('edit','waiting_validation',attempt)
            wait_validation(key,attempt,base,bot_token,manifest['test_token'],acceptance)
            results=[transfer.json(f'result-{attempt}-{platform}') for platform in ('windows',)]
            digest=hashlib.sha256(workspace.regression['code'].encode()).hexdigest()
            hashes=[f['sha256'] for f in manifest['files']]
            if verified(results,digest,hashes):
                if acceptance or benchmark:
                    benchmark_verified=True
                    Path('acceptance.json').write_text(json.dumps({'verified':True,'attempts':attempt,'sample_count':len(hashes),'cost':spent}),encoding='utf-8')
                    print(f'Acceptance passed after {attempt} rounds; no public source was changed.',flush=True)
                    return
                transfer.progress('edit','publishing',attempt)
                sha=worker.publish(workspace,branch,key,'Targeted Windows regression passed on baseline/fixed comparison; full regression suites run on the PR; macOS validation is disabled.',token,base)
                with open(os.environ['GITHUB_OUTPUT'],'a') as out: out.write(f'commit={sha}\n')
                receipt={'job_id':key,'sha':sha,'regression_verified':True,'attempts':attempt,'sample_count':len(hashes),'test_sha256':digest,'cost':spent}
                Path('source.json').write_text(json.dumps(receipt),encoding='utf-8')
                transfer.progress('edit','packaging',attempt)
                print('Private regression verified; ready for public packaging.',flush=True)
                return
            feedback=failure_feedback(results)
            print('Regression did not pass; returning diagnostics to the AI.',flush=True)
        raise RuntimeError('Regression still fails after the bounded repair attempts; no success reported')
    finally:
        if benchmark:
            ws=locals().get('workspace')
            Path('benchmark-result.json').write_text(json.dumps({
                'job_id':key,'base':base,'verified':benchmark_verified,
                'cost':getattr(ws,'last_cost',0),'model_calls':getattr(ws,'model_turns',0),
                'usage':getattr(ws,'usage_events',[]),
                'exit_reason':locals().get('exit_reason','setup_failed'),
                'attempts':locals().get('attempt',0)}),encoding='utf-8')
        shutil.rmtree(samples,ignore_errors=True)

if __name__=='__main__': main()
