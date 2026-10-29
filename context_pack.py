"""Deterministic, bounded evidence for generation and focused repair rounds."""
from collections import Counter
import json
import re
import subprocess

STOP = set('i want the a an and or to for of in on it is be can have has will this that with as by from '
           'please should would could need allow ability though through become while helpful make do '
           'my me we our you your not both then than also editor feature request project'.split())


def query_terms(request):
    words = re.findall(r'[A-Za-z_][A-Za-z0-9_]*',request)
    counts=Counter(w.lower() for w in words if w.lower() not in STOP and len(w)>2)
    return [word for word,count in counts.most_common(8) for _ in range(min(count,4))]


def evidence_pack(workspace, request, feedback=''):
    workspace.code_search(' '.join(query_terms(request)))
    index = workspace.code_index
    pieces = []
    remaining = 18000
    seen = set()
    source_headers = {}
    def header(path):
        if path in source_headers: return
        try:
            body = workspace.path(path).read_text(encoding='utf-8-sig')
            declarations = re.findall(r'^\s*(?:global\s+)?(?:using\s+[^\n]+|namespace\s+[^\n{;]+[;{]?)', body, re.M)
            source_headers[path] = '\n'.join(line.strip() for line in declarations)
        except (OSError, ValueError):
            pass
    def add(label, body, cap):
        nonlocal remaining
        raw = (label+'\n'+body).encode()[:min(cap,remaining)]
        text = raw.decode('utf-8',errors='ignore')
        if text:
            pieces.append(text)
            remaining -= len(text.encode())
    if feedback:
        add('FAILED CHECKS (diagnostic data)',feedback,3500)
        diff = subprocess.check_output(['git','-C',str(workspace.root),'diff','--no-ext-diff','--unified=3','HEAD','--',*sorted(workspace.changed)],text=True)
        tracked=set(subprocess.check_output(['git','-C',str(workspace.root),'ls-files'],text=True).splitlines())
        for name in sorted(workspace.changed-tracked):
            diff+='\nNEW FILE '+name+'\n'+workspace.path(name).read_text(encoding='utf-8')
        add('CURRENT PATCH',diff,7000)
        if getattr(workspace,'regression',None):
            add('CURRENT PRIVATE REGRESSION',json.dumps(workspace.regression),5000)
    else:
        for path in ['AGENTS.md','docs/EDITING_PERFORMANCE.md']:
            try:
                add(path,workspace.path(path).read_text(encoding='utf-8'),2500)
            except (OSError,ValueError):
                pass
    query = ' '.join(query_terms(request))
    code_pieces=[]
    primary_symbol=''
    primary_owner=''
    for prefix,limit,cap in [('src/FruitsAtelier.Core/',1,3500),('src/FruitsAtelier.App/',2,4000),('tests/',1,2500)]:
        selected=0
        hits=[]
        if primary_symbol:
            hits.extend(index.search(primary_symbol,limit=3,budget=14000,prefix=prefix))
        for term in list(dict.fromkeys(query_terms(request)))[:2]:
            hits.extend(index.search(term,limit=3,budget=14000,prefix=prefix))
        hits.extend(index.search(query,limit=6,budget=20000,prefix=prefix))
        # Interleave the two main concepts rather than letting a common noun fill the pack.
        hits=hits[::3]+[hit for i,hit in enumerate(hits) if i%3]
        for hit in hits:
            if len(hit['text'])<180: continue
            marker=(hit['path'],hit['start'])
            if marker in seen: continue
            seen.add(marker)
            header(hit['path'])
            add(f'{hit["path"]}:{hit["start"]}-{hit["end"]} {hit["symbol"]}',hit['text'],cap)
            code_pieces.append(hit['text'])
            if not primary_symbol:
                primary_symbol=hit['symbol'].rsplit('.',1)[-1]
                primary_owner=hit['symbol'].rsplit('.',1)[0]
            selected+=1
            if selected>=limit: break
    # Known signatures and type shapes reduce invented API calls without repeating method bodies.
    identifiers = list(dict.fromkeys(re.findall(r'\b[A-Z][A-Za-z0-9_]{3,}\b','\n'.join(code_pieces))))[:20]
    signatures=[]
    for symbol in identifiers:
        for path,start,end,name,body in index.db.execute(
                'SELECT path,start,end,symbol,body FROM chunks WHERE symbol=? OR symbol=? OR symbol LIKE ? LIMIT 4',
                (symbol,primary_owner+'.'+symbol,symbol+'.%')):
            line=body.splitlines()[0] if body else ''
            header(path)
            signatures.append(f'{path}:{start} {name}: {line[:200]}')
    add('AVAILABLE API SIGNATURES (use actual members; inspect missing definitions)', '\n'.join(signatures),2500)
    add('ACTUAL C# IMPORTS AND NAMESPACES (directories are not namespaces)',
        '\n\n'.join(path+'\n'+body for path,body in source_headers.items()),2000)
    return '\n\n'.join(pieces)


def concise_failures(results):
    lines=[]
    def visit(value,path=''):
        if isinstance(value,dict):
            if value.get('exit',0)!=0:
                lines.append(f'{path}: exit={value["exit"]}')
                output=value.get('output','')
                relevant=[line.strip() for line in output.splitlines()
                          if re.search(r'error [A-Z]+\d+|^FAIL |Exception|expected|actual',line,re.I)]
                lines.extend(relevant or output.splitlines()[-5:])
            if value.get('error'): lines.append(f'{path}: {value["error"]}')
            for key,child in value.items():
                if key!='output': visit(child,path+'/'+key)
        elif isinstance(value,list):
            for i,child in enumerate(value): visit(child,path+'/'+str(i))
    visit(results)
    return '\n'.join(dict.fromkeys(lines))[:3500]
