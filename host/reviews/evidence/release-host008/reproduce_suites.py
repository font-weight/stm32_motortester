"""Reproduce release suites into critic-owned evidence only."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(__file__).resolve().parent
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
           PYTHONPATH=str(ROOT/'host') + ':/tmp/motortester-deps',
           QT_QPA_PLATFORM='offscreen')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def source_hashes():
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(ROOT.rglob('*'))
            if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts}
before = source_hashes()
runner = str(OUT/'run_suites.py')
runs = []
commands = [
 ('imports', [sys.executable, '-c', 'import sys,platform,numpy,PySide6,pyqtgraph; from motortester import session,window,device_emulator; print(sys.version); print(platform.platform()); [print(m.__name__,getattr(m,"__version__","n/a"),m.__file__) for m in (numpy,PySide6,pyqtgraph,session,window,device_emulator)]']),
 ('release-normal', [sys.executable, runner, str(ROOT/'host/tests'), 'test_session_release*.py']),
 ('release-optimized', [sys.executable, '-O', runner, str(ROOT/'host/tests'), 'test_session_release*.py']),
 ('existing-session', [sys.executable, runner, str(ROOT/'host/tests'), 'test_session.py', 'test_session_conformance.py']),
 ('full-normal', [sys.executable, runner, str(ROOT/'host/tests'), 'test*.py']),
 ('full-optimized', [sys.executable, '-O', runner, str(ROOT/'host/tests'), 'test*.py']),
]
for name,cmd in commands:
    path = OUT/(name+'.log')
    started=time.monotonic()
    with path.open('w') as f:
        f.write('cwd='+str(ROOT)+'\ncommand='+json.dumps(cmd)+'\n')
        f.flush()
        result=subprocess.run(cmd,cwd=ROOT,env=ENV,stdout=f,stderr=subprocess.STDOUT,timeout=900)
    lines=[x.partition('=')[2] for x in path.read_text().splitlines() if x.startswith('CRITIC_RESULT=')]
    entry=dict(name=name,command=cmd,returncode=result.returncode,seconds=round(time.monotonic()-started,3),log=path.name,sha256=digest(path))
    if name!='imports': entry['summary']=json.loads(lines[0]) if len(lines)==1 else None
    runs.append(entry)
    print(name,entry['returncode'],entry.get('summary'),flush=True)
after=source_hashes()
manifest=dict(root=str(ROOT),python=sys.version,platform=platform.platform(),
              environment={k:ENV[k] for k in ('PYTHONDONTWRITEBYTECODE','PYTHONPATH','QT_QPA_PLATFORM')},
              sources_before=before,sources_after=after,source_drift={k:[before.get(k),after.get(k)] for k in before.keys()|after.keys() if before.get(k)!=after.get(k)},runs=runs)
manifest['success']=not manifest['source_drift'] and all(r['returncode']==0 for r in runs)
(OUT/'suite-runs.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
raise SystemExit(0 if manifest['success'] else 1)
