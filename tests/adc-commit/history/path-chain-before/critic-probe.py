from pathlib import Path
import subprocess,json,hashlib,os,time,signal
root=Path(__file__).resolve().parent; tool='/workspace/shared/arm-resource-evidence/toolchain/root/usr';results=[]
subjects={'adc003-r3':Path('/workspace/shared/critic-a-adc003-package-review/checkout-r3/tests/adc-commit/reproduce.py'),'tx002-r2':Path('/workspace/shared/critic-a-tx002-package-review/checkout/tests/tx-stop-stack/reproduce.py')}
for name,source in subjects.items():
 d=root/name;d.mkdir();lex=d/'invocation';mid=d/'intermediate';lex.mkdir();mid.mkdir(); entry=lex/'reproduce.py'; hop=mid/'reproduce.py';hop.symlink_to(source);entry.symlink_to(hop);before=hashlib.sha256(source.read_bytes()).hexdigest(); out=mid/'accepted-output';cmd=['/usr/bin/python3','-I','-B',str(entry),'--toolchain',tool,'--out',str(out)]
 pre=None
 if name.startswith('tx'):
  p=subprocess.run(cmd+['--preflight-only'],text=True,capture_output=True,timeout=40);pre={'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr};(d/'preflight.json').write_text(json.dumps(pre,indent=2)+'\n')
 with (d/'run.log').open('w') as log:
  p=subprocess.Popen(cmd,text=True,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);deadline=time.monotonic()+40
  while p.poll() is None and not out.exists() and time.monotonic()<deadline:time.sleep(.02)
  created=out.is_dir();stopped=False
  if p.poll() is None:
   stopped=True;os.killpg(p.pid,signal.SIGTERM)
  rc=p.wait(timeout=10)
 assert hashlib.sha256(source.read_bytes()).hexdigest()==before
 results.append({'subject':name,'script_sha256':before,'argv':cmd,'symlink_chain':[str(entry),str(hop),str(source)],'direct_file_symlinks':entry.is_symlink() and hop.is_symlink(),'intermediate_parent':str(mid),'output_created':created,'exit_code':rc,'stopped_by_reviewer_after_admission':stopped,'files_created':sum(x.is_file() for x in out.rglob('*')) if created else 0,'script_unchanged':True,'preflight_only':pre})
(root/'results.json').write_text(json.dumps({'scope':'Bounded writer-admission check only; child deliberately terminated after output creation, no full test result claimed. Stable supported two-file-symlink chain, three distinct parents. Production and frozen package unchanged.','results':results},indent=2)+'\n');print(json.dumps(results,indent=2))
