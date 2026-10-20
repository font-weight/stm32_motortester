from pathlib import Path
import concurrent.futures, json, os, re, subprocess, sys, time

R=Path('/tmp/notice-supplement-critic'); E=R/'evidence'; source=R/'source'
env=os.environ.copy()
env.update(PYTHONPATH=str(source/'host')+':/workspace/shared/motortester-runtime-recovery-20261012/deps', PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0', QT_QPA_PLATFORM='offscreen')
def run(mode):
    result=[]
    for module,expected in [('license_packaging',14),('serial_packaging',37),('windows_smoke',26),('windows_archive',4)]:
        cmd=[sys.executable]+(['-O'] if mode=='optimized' else [])+['-m','unittest','discover','-s','tests','-p','test_'+module+'.py','-v']
        start=time.monotonic()
        p=subprocess.run(cmd,cwd=source/'host',env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        log=E/(mode+'-'+module+'.log');log.write_bytes(p.stdout)
        text=p.stdout.decode('utf-8',errors='replace')
        count=re.findall(r'Ran (\d+) tests? in ([0-9.]+)s',text)
        valid=p.returncode==0 and len(count)==1 and int(count[0][0])==expected and '\nOK\n' in text and 'skipped=' not in text
        result.append({'module':module,'command':cmd,'exit':p.returncode,'tests':count,'zero_skips':valid,'elapsed_seconds':time.monotonic()-start,'log':log.name})
        if not valid:raise RuntimeError(str(result[-1]))
    return mode,result
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results=dict(pool.map(run,['normal','optimized']))
(E/'FOCUSED-RESULTS.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
