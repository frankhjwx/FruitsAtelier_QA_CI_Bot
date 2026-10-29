"""Unprivileged runner: exact-sample baseline/fix comparison and regression suites."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from ci_transfer import Transfer
from worker import LOCALIZATION_FILES

HARNESS = r'''
using System;
using System.IO;
using System.Collections.Generic;
public sealed class RegressionFailure : Exception { public RegressionFailure(string message) : base(message) {} }
public sealed class Sample {
    private readonly string path;
    public string Name { get; }
    public static int Reads;
    public Sample(string path, string name) { this.path=path; Name=name; }
    public string Path { get { Reads++; return path; } }
    public byte[] ReadAllBytes() { Reads++; return File.ReadAllBytes(path); }
    public string ReadAllText() { Reads++; return File.ReadAllText(path); }
}
public static class Entry {
    public static int Main() {
        try {
            var paths=System.Text.Json.JsonDocument.Parse(File.ReadAllText(Environment.GetEnvironmentVariable("FA_SAMPLE_MANIFEST")!));
            var samples=new List<Sample>();
            foreach(var s in paths.RootElement.EnumerateArray()) samples.Add(new Sample(s.GetProperty("path").GetString()!,s.GetProperty("name").GetString()!));
            Regression.Run(samples);
            if(samples.Count>0 && Sample.Reads==0) { Console.Error.WriteLine("Regression did not use an original sample"); return 43; }
            return 0;
        } catch(RegressionFailure error) {
            if(Environment.GetEnvironmentVariable("FA_HAS_SAMPLES")=="1" && Sample.Reads==0) return 43;
            Console.Error.WriteLine(error.Message); return 42;
        } catch(Exception error) { Console.Error.WriteLine(error); return 44; }
    }
}
'''


def diagnostic_output(raw):
    text=raw.decode('utf-8',errors='replace')
    lines=text.splitlines(); selected=[]
    for index,line in enumerate(lines):
        if line.startswith('FAIL ') or ': error ' in line or 'Unhandled exception' in line:
            selected.extend(lines[max(0,index-1):index+5])
    failures='\n'.join(selected)[:8000]
    return (failures+'\n[Log tail]\n'+text[-6000:]) if failures else text[-14000:]


def run(command,cwd,deadline,env=None):
    remaining=min(900,deadline-time.time())
    if remaining<=0: raise TimeoutError('Validation deadline reached')
    env = dict(os.environ) if env is None else dict(env)
    env = {k:v for k,v in env.items() if not k.startswith(('TRANSFER_', 'GITHUB_', 'ACTIONS_'))}
    result=subprocess.run(command,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=remaining)
    return {'exit':result.returncode,'output':diagnostic_output(result.stdout)}


def apply_changes(root,changes):
    if not 1<=len(changes)<=8: raise ValueError('Invalid change count')
    for name,body in changes.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or path.parts[0] not in {'src','tests','docs','macOS'} or (path.suffix not in {'.cs','.md','.resx'} and name not in LOCALIZATION_FILES) or name.endswith('AGENTS.md'):
            raise ValueError('Invalid candidate path')
        dest=(root/path).resolve()
        if not dest.is_relative_to(root.resolve()) or dest.is_symlink() or len(body.encode())>2_000_000:
            raise ValueError('Invalid candidate file')
        dest.parent.mkdir(parents=True,exist_ok=True); dest.write_text(body,encoding='utf-8')


def regression(root,private,code,target,samples,deadline):
    private.mkdir(parents=True,exist_ok=True)
    assembly='FruitsAtelier.'+('App' if target=='app' else 'Core')+'.Tests'
    reference=root/'src'/('FruitsAtelier.App' if target=='app' else 'FruitsAtelier.Core')/(('FruitsAtelier.App' if target=='app' else 'FruitsAtelier.Core')+'.csproj')
    framework='net10.0-windows' if target=='app' else 'net10.0'
    from xml.sax.saxutils import escape
    (private/'Regression.csproj').write_text(f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>{framework}</TargetFramework><AssemblyName>{assembly}</AssemblyName><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable><PlatformTarget>x64</PlatformTarget></PropertyGroup><ItemGroup><ProjectReference Include="{escape(str(reference))}" /></ItemGroup></Project>',encoding='utf-8')
    (private/'Harness.cs').write_text(HARNESS,encoding='utf-8')
    (private/'Regression.cs').write_text(code,encoding='utf-8')
    build=run(['dotnet','build',str(private/'Regression.csproj'),'-c','Release','--nologo'],root,deadline)
    if build['exit']!=0: return {'build':build,'run':None}
    env=dict(os.environ); env['FA_SAMPLE_MANIFEST']=str(samples); env['FA_HAS_SAMPLES']='1' if json.loads(samples.read_text()) else '0'
    result=run(['dotnet',str(private/'bin'/'Release'/framework/(assembly+'.dll'))],root,deadline,env)
    return {'build':{'exit':0},'run':result}


def main():
    transfer=Transfer(os.environ['TRANSFER_URL'],os.environ['TRANSFER_FINGERPRINT'],os.environ['JOB_ID'],os.environ['TRANSFER_TOKEN'])
    attempt=int(os.environ['ATTEMPT']); platform=os.environ['VALIDATION_PLATFORM']
    root=Path(os.environ['RUNNER_TEMP'])/('fa-private-'+os.environ['JOB_ID']+'-'+str(attempt))
    baseline=Path(os.environ['SOURCE_ROOT']).resolve()
    root.mkdir(parents=True,exist_ok=True)
    result={'passed':False,'platform':platform,'attempt':attempt}
    try:
        manifest=transfer.download(root/'samples',minimum_remaining=1800)
        deadline=min(time.time()+1200,manifest['expires']-60)
        candidate=transfer.json(f'candidate-{attempt}')
        if subprocess.check_output(['git','-C',str(baseline),'rev-parse','HEAD'],text=True).strip()!=candidate['base']:
            raise ValueError('Baseline identity mismatch')
        sample_index=root/'samples.json'
        sample_index.write_text(json.dumps([{'path':str(root/'samples'/f['file']),'name':f['name']} for f in manifest['files']]),encoding='utf-8')
        acceptance=os.getenv('FA_ACCEPTANCE')=='true'
        if acceptance:
            (baseline/'src/FruitsAtelier.Core/PapayaAcceptance.cs').write_text('namespace FruitsAtelier; public static class PapayaAcceptance { public static bool IsCatch(int mode) => mode == 0; }',encoding='utf-8')
        fixed=root/'fixed'
        shutil.copytree(baseline,fixed,ignore=shutil.ignore_patterns('.git','artifacts','bin','obj'))
        apply_changes(fixed,candidate['changes'])
        if acceptance and attempt==1:
            (fixed/'src/FruitsAtelier.Core/PapayaAcceptance.cs').write_text('namespace FruitsAtelier; public static class PapayaAcceptance { public static bool IsCatch(int mode) => mode == 0; }',encoding='utf-8')
            result['acceptance_injected_failure']='Acceptance deliberately restored the faulty fixture for round one; resubmit the correct fix and keep the test unchanged.'
        result['test_sha256']=hashlib.sha256(candidate['regression']['code'].encode()).hexdigest()
        result['sample_hashes']=[f['sha256'] for f in manifest['files']]
        if platform=='macos':
            # Avoid full platform builds for a candidate already rejected by the Windows gate.
            while time.time()<deadline:
                try:
                    gate=transfer.json(f'result-{attempt}-windows')
                    break
                except RuntimeError as error:
                    if 'HTTP 404' not in str(error): raise
                    time.sleep(10)
            else: raise TimeoutError('Windows validation gate did not finish')
            if not gate.get('passed'):
                result['skipped']='Windows baseline/fixed or regression gate failed'
                return
        if platform=='windows':
            for label,source in [('baseline',baseline),('fixed',fixed)]:
                if candidate['regression']['target']=='app':
                    print(f'Preparing {label} application dependencies.',flush=True)
                    prep=run([sys.executable,'scripts/Build-MapsetVerifier.py'],source,deadline)
                    if prep['exit']!=0: result[label]={'prepare':prep}; return
                print(f'Running {label} sample regression.',flush=True)
                stage_samples=root/(label+'-samples')
                stage_samples.mkdir()
                for entry in manifest['files']:
                    original=root/'samples'/entry['file']
                    if hashlib.sha256(original.read_bytes()).hexdigest()!=entry['sha256']:
                        raise ValueError('Original sample changed during validation')
                    shutil.copyfile(original,stage_samples/entry['file'])
                stage_index=root/(label+'-samples.json')
                stage_index.write_text(json.dumps([{'path':str(stage_samples/f['file']),'name':f['name']} for f in manifest['files']]),encoding='utf-8')
                result[label]=regression(source,root/(label+'-test'),candidate['regression']['code'],candidate['regression']['target'],stage_index,deadline)
                if result[label]['build']['exit']!=0:
                    return
            before=result['baseline']; after=result['fixed']
            if (before.get('run') or {}).get('exit')!=42 or (after.get('run') or {}).get('exit')!=0:
                return
            print('Preparing fixed-source regression suites.',flush=True)
            prep=run([sys.executable,'scripts/Build-MapsetVerifier.py'],fixed,deadline)
            if prep['exit']!=0: result['prepare']=prep; return
            build=run(['dotnet','build','FruitsAtelier.sln','-c','Release','-p:RestoreLockedMode=true','--nologo'],fixed,deadline)
            result['build']=build
            if build['exit']!=0: return
            results=[]
            for suite,args in [('Core',[]),('Gameplay',[]),('App',[]),('Skinning',[]),('SkinArchive',[]),('Formats',['--skip-external-fixtures']),('Audio',['--hitsound-check']),('App',['--mapset-verifier-engine-check'])]:
                check=run(['dotnet','run','--no-build','--project',f'tests/FruitsAtelier.{suite}.Tests','-c','Release']+(['--']+args if args else []),fixed,deadline)
                results.append({'suite':suite,**check})
                result['suites']=results
                if check['exit']!=0: return
        else:
            print('Preparing macOS regression suites.',flush=True)
            prep=run([sys.executable,'scripts/Build-MapsetVerifier.py'],fixed,deadline)
            result['prepare']=prep
            if prep['exit']!=0: return
            check=run(['bash','scripts/Test-Mac.sh','--skip-device-tests'],fixed,deadline)
            result['suites']=[check]
            if check['exit']!=0: return
        result['passed']=True
    except Exception as error:
        result['error']=type(error).__name__
    finally:
        # Test output may include user data: return only through the expiring private endpoint.
        try: transfer.json(f'result-{attempt}-{platform}',result)
        finally: shutil.rmtree(root,ignore_errors=True)
        print('Private validation passed.' if result['passed'] else 'Private validation needs further repair.',flush=True)

if __name__=='__main__': main()
