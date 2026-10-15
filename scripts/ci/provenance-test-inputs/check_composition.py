"""Opaque two-collector/prelaunch composition; no Windows/build/native execution."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT=Path(os.environ['SOURCE_COLLECTOR_INPUT_ROOT'])
TEST=ROOT/'source-collector-rebuild-20261015/scripts/ci/replacement-source-inputs/test_source_inputs.py'
spec=importlib.util.spec_from_file_location('composition_fixtures',TEST)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory(prefix='opaque-source-prelaunch-composition-') as folder:
    f=m.Fixture(Path(folder));receipts={}
    receipts['control']=f.run()
    control=f.config['source_output'].read_bytes()
    modified,_=m.f.c.modification(m.f.ORIGINAL,m.f.TRIAL)
    m.put(f.target,modified)
    f.variant='modified';f.config['variant']='modified'
    f.config['exe']=f.pre.root/'modified/bundle/Motortester.exe'
    f.work=f.pre.root/'modified/PYZ-00.pyz';f.config['work_pyz']=f.work
    f.config['source_output']=f.base/'results/modified-source-inputs.json'
    f.config['sidecar_output']=f.base/'results/modified-sidecar.json'
    f.save();receipts['modified']=f.run()
    m.put(f.pre.root/'control/source-inputs.json',control)
    m.put(f.pre.root/'modified/source-inputs.json',f.config['source_output'].read_bytes())
    f.pre.refresh();prelaunch=f.pre.run()
    if prelaunch['status']!='PRELAUNCH_BYTES_MATCH':raise RuntimeError('prelaunch status')
    if not all(prelaunch[k] is False for k in m.f.p.FALSE_FIELDS):raise RuntimeError('prelaunch assurance')
    if not all(row[k] is False for row in receipts.values() for k in m.s.FALSE_FIELDS):raise RuntimeError('source assurance')
    print(json.dumps({'scope':'opaque-portable-composition-only','collector_receipts':receipts,
                      'prelaunch_receipt':prelaunch,'candidate_executions':0,
                      'actual_windows_execution':False},sort_keys=True))
