#!/usr/bin/env python3
"""Executable future harness contract. No driver => NOT_RUN, never PASS."""
import argparse, json, pathlib, subprocess, sys
p=argparse.ArgumentParser(); p.add_argument('--driver'); p.add_argument('--case',action='append'); p.add_argument('--out')
a=p.parse_args(); root=pathlib.Path(__file__).resolve().parent.parent
matrix=json.loads((root/'acceptance_matrix.json').read_text()); results=[]
for c in matrix['cases']:
    if a.case and c['id'] not in a.case: continue
    for mode in c['modes']:
        for operation in c.get('operations_by_mode', {}).get(mode, c['operations']):
            row=dict(case=c['id'],mode=mode,operation=operation,status='NOT_RUN')
            if a.driver:
                cmd=[a.driver,'--case',c['id'],'--mode',mode,'--operation',operation]
                try:
                    run=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
                    data=json.loads(run.stdout)
                    assert run.returncode==0, f'exit={run.returncode}: {run.stderr}'
                    assert all(data.get(k)==row[k] for k in ['case','mode','operation']), 'identity mismatch'
                    obs=data['observations']; mismatches=[]
                    for k,want in c['expected'].items():
                        got=obs.get(k)
                        if type(got) is not type(want) or got!=want: mismatches.append(f'{k}: expected {want!r}, got {got!r}')
                    assert not mismatches, '; '.join(mismatches)
                    row.update(status='PASS',observations=obs)
                except Exception as e: row.update(status='FAIL',reason=str(e))
            results.append(row)
            print(f"{row['status']} {row['case']} {mode} {operation}")
if a.out: pathlib.Path(a.out).write_text(json.dumps(results,indent=2)+'\n')
if not results: sys.exit('No matching cases')
print(f'{len(results)} case/mode/operation runs; future bridge assertions, not silicon proof')
sys.exit(2 if not a.driver else 1 if any(r['status']!='PASS' for r in results) else 0)
