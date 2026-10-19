#!/usr/bin/env python3
"""Read an unchanged historical frozen proof; repair review copies only."""
import sys
if sys.flags.optimize != 0 or not sys.flags.isolated or not sys.flags.dont_write_bytecode:
 sys.stderr.write('REJECTED_INTERPRETER_POLICY: require python3 -I -B with assertions enabled; -O/-OO are forbidden\n');raise SystemExit(2)
import argparse,pathlib,json,hashlib,shutil,subprocess,datetime,uuid
p=argparse.ArgumentParser();p.add_argument('--proof',required=True,type=pathlib.Path);a=p.parse_args();O=pathlib.Path(__file__).resolve().parent;P=a.proof.resolve();D=O/'runs'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]);D.mkdir(parents=True,exist_ok=False);W=D/'work';W.mkdir()
def require(x,m):
 if not x:raise RuntimeError(m)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
m=json.load(open(P/'manifest.json'));before={rel:sha(P/rel)for rel in m['files']}
require(all(before[rel]==h for rel,h in m['files'].items()),'original manifest drift')
require(sha(P/'image/DIAGNOSTIC_UNQUALIFIED_NEVER_FLASH.elf')=='1480750bf9802e90a543369f03922ff060dd61c42fde7bfaf1b8eca19bd60bb2','wrong old image')
for rel in m['files']:
 dst=W/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(P/rel,dst)
for name in ['trace_callbacks.py','check_sp.py']:
 f=W/name;s=f.read_text();require('[0-9a-f]{4,8}'in s,('missing known parser pattern',name));f.write_text(s.replace('[0-9a-f]{4,8}','(?:[0-9a-f]{4}|[0-9a-f]{8})'))
for name in ['trace_callbacks.py','check_sp.py','derive_bound.py']:
 r=subprocess.run([sys.executable,'-I','-B',str(W/name)],capture_output=True,text=True);(D/(name+'.stdout')).write_text(r.stdout);(D/(name+'.stderr')).write_text(r.stderr);require(r.returncode==0,('analysis failed',name))
old=json.load(open(P/'sp-cfg-analysis.json'));new=json.load(open(W/'sp-cfg-analysis.json'));require(not new['issues'],'SP issues');require(old['call_sites']==new['call_sites'],'graph edge delta')
changes=[]
for f,g in zip(old['functions'],new['functions']):
 require(f['address']==g['address']and f['max_frame']==g['max_frame']and f['returns']==g['returns'],'frame/return delta')
 if f!=g:changes.append({'function':f['name'],'address':f['address'],'instructions_visited_before':f['instructions_visited'],'instructions_visited_after':g['instructions_visited']})
require(len(changes)==1 and changes[0]['instructions_visited_after']==changes[0]['instructions_visited_before']+1,'unexpected lexical delta')
ob=json.load(open(P/'static-bound.json'));nb=json.load(open(W/'static-bound.json'));require(ob==nb,'numeric bound delta');after={rel:sha(P/rel)for rel in m['files']};require(before==after,'original proof mutated')
r={'status':'HISTORICAL_PARSER_CORRECTION_REPRODUCED','elf_sha256':sha(P/'image/DIAGNOSTIC_UNQUALIFIED_NEVER_FLASH.elf'),'defect':'greedy 4..8 hex-character field consumed addcc mnemonic, causing CFG to skip following uxth','repair':'accept exactly4 or8 hex characters per objdump word; preserve addcc as mnemonic and next uxth as instruction','lexical_delta':changes,'call_edges_identical':True,'frames_identical':True,'full_static_bound_json_identical':True,'foreground_bytes':nb['foreground_bytes'],'usb_software_bytes':nb['usb_software_bytes'],'total_upper_bound_bytes':nb['total_upper_bound_bytes'],'eight_byte_reservation_minimum':nb['eight_byte_reservation_minimum'],'original_frozen_proof_unchanged':True,'scope':'Evidence repair for exhaustive-walk claim only; no source/image/hardware/timing acceptance change'}
(D/'parser-delta.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
