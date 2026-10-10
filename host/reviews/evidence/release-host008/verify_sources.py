"""Verify frozen bytes and supplied evidence against the exact local Git head."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(sys.argv[1]).resolve()
FROZEN=Path(sys.argv[2]).resolve()
OUT=Path(__file__).resolve().parent
HEAD='45ef38792b8e824e2109e7bacc1c9dc605917d06'
BASE='ef2880c0655d155284c176424a12378b3b66adf4'
def git(*args): return subprocess.check_output(['git','-C',str(ROOT),*args])
def sha(raw): return hashlib.sha256(raw).hexdigest()
files=git('ls-tree','-r','--name-only',HEAD).decode().splitlines()
entries={}
for name in files:
    blob=git('show',HEAD+':'+name)
    p=FROZEN/name
    entries[name]={'git_sha256':sha(blob),'local_sha256':sha(p.read_bytes()) if p.is_file() else None}
    entries[name]['match']=entries[name]['git_sha256']==entries[name]['local_sha256']
local={str(p.relative_to(FROZEN)) for p in FROZEN.rglob('*') if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts}
manifest_path='host/tests/fixtures/session_release/evidence/verified-manifest.json'
manifest_raw=(FROZEN/manifest_path).read_bytes()
m=json.loads(manifest_raw)
checks={}
for category in ('sources_before','sources_after','evidence_sha256'):
    entries_m={name:{'expected':expected,'actual':sha((FROZEN/name).read_bytes())}
               for name,expected in m[category].items()}
    checks[category]={'count':len(entries_m),'mismatches':{n:d for n,d in entries_m.items() if d['expected']!=d['actual']}}
publication=[]
for key in ('implementation_publication_files','integration_publication_files','conformance_publication_files'):
    publication.extend(m[key])
actual_diff=git('diff','--name-only',BASE,HEAD).decode().splitlines()
sources={}
for pin,paths in {
 'b6ae8025baf1a47e8173997029d2d8b858ada74c':['protocol/wire-v0.1.md','docs/session-guard.md'],
 '124b3f32e1e7f7282bce283baa4c9406d4d72950':['protocol/wire-v0.1.md'],
 'c54bd955aaf3cb863ef3df043ea237ea62e74ad4':['protocol/wire-v0.1.md','docs/session-guard.md']}.items():
    for path in paths:
        raw=git('show',pin+':'+path)
        filename='source-'+pin[:7]+'-'+Path(path).name
        (OUT/filename).write_bytes(raw)
        sources[pin+':'+path]={'sha256':sha(raw),'evidence_file':filename}
report=dict(head=HEAD,base=BASE,tree=git('rev-parse',HEAD+'^{tree}').decode().strip(),
 parent=git('show','-s','--format=%P',HEAD).decode().strip(),
 checked_files=len(entries),files=entries,untracked_extras=sorted(local-set(files)),
 manifest_path=manifest_path,manifest_sha256=sha(manifest_raw),
 supplied_manifest_checks=checks,publication_files=sorted(publication),
 exact_diff_files=actual_diff,publication_matches_diff=set(publication)==set(actual_diff),
 source_documents=sources)
report['success']=(all(e['match'] for e in entries.values()) and not report['untracked_extras']
 and report['manifest_sha256']=='a4596d0f5eea1318841390600bcc763d9aa02ad66649ec4a06fece3de37a0042'
 and all(not c['mismatches'] for c in checks.values()) and report['publication_matches_diff']
 and report['parent']==BASE and report['tree']=='e39dfe24663d089e8ff9888101305a0e2af8a626')
(OUT/'source-verification.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
(OUT/'diff-scope.txt').write_bytes(git('diff','--stat',BASE,HEAD)+b'\n'+git('diff','--numstat',BASE,HEAD))
print(json.dumps({k:v for k,v in report.items() if k not in ('files','publication_files','exact_diff_files','source_documents')},indent=2))
raise SystemExit(0 if report['success'] else 1)
