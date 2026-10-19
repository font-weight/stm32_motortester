#!/usr/bin/env python3
"""Strict declaration API/CLI controls; no physical qualification."""
import argparse, hashlib, importlib.util, json, os, subprocess, sys
from pathlib import Path

def need(ok,msg):
 if not ok: raise ValueError(msg)
def main():
 need(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,'use python3 -I -B without -O')
 p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args(); source,out=a.source.resolve(),a.output
 need(out.is_absolute() and out.resolve()==out and not out.exists() and out.parent.is_dir(),'fresh normalized absolute output required'); out.mkdir()
 script=source/'firmware/build/stream_timing.py'; raw_script=script.read_bytes(); spec=importlib.util.spec_from_file_location('timing_under_test',script); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
 base={'schema_version':1,'input_kind':'diagnostic_provisional','declared_transaction_bound_ms':1,'source_commit':'0123456789abcdef0123456789abcdef01234567','profile':'stream-only-v1'}; results={}
 def record(label,fn,accepted):
  try: value=fn(); observed=True
  except (ValueError,TypeError,UnicodeError,OverflowError): observed=False; value=None
  need(observed==accepted,label+' wrong acceptance'); results[label]={'pass':True,'accepted':observed}; return value
 def check_data(label,change,accept=False):
  d=dict(base); d.update(change); return record(label,lambda:mod.parse(json.dumps(d).encode()),accept)
 for bound in range(1,500):
  d=check_data('valid_bound_'+str(bound),{'declared_transaction_bound_ms':bound},True); text=mod.generate(d); need('.declared_transaction_bound_ms = '+str(bound)+'u' in text and 'NOT TIMING PROOF OR FLASH APPROVAL' in text,'wrong generated C')
 for key in base:
  d=dict(base); del d[key]; record('missing_'+key,lambda d=d:mod.parse(json.dumps(d).encode()),False)
  text=json.dumps(base); extra=json.dumps(key)+':'+json.dumps(base[key]); record('duplicate_'+key,lambda text=text,extra=extra:mod.parse((text[:-1]+','+extra+'}').encode()),False)
  for kind in ('parse','generate'):
   d=dict(base); d['unexpected']=None; record(kind+'_extra_'+key,lambda d=d,kind=kind:getattr(mod,kind)(json.dumps(d).encode() if kind=='parse' else d),False)
 bad_bounds=[-1,0,500,501,2**32,2**128,True,False,None,[],{},'1',1.0,1e100,float('nan'),float('inf'),-float('inf')]
 for i,v in enumerate(bad_bounds):
  check_data('bad_bound_'+str(i),{'declared_transaction_bound_ms':v}); d=dict(base,declared_transaction_bound_ms=v); record('generate_bad_bound_'+str(i),lambda d=d:mod.generate(d),False)
 for i,v in enumerate((0,2,-1,True,False,None,'1',1.0,{},[])): check_data('bad_schema_'+str(i),{'schema_version':v})
 for field,values in [('input_kind',['qualified','diagnostic','DIAGNOSTIC_PROVISIONAL','diagnostic_provisional ',True,1,None,[],{}]),('profile',['legacy','stream-only-v2','STREAM-ONLY-V1','stream-only-v1\n',None,0,True]),('source_commit',['0'*39,'0'*41,'G'*40,'A'*40,'0'*39+'\n','0'*39+' ',True,1,None,[],{},'../'+'0'*37])]:
  for i,v in enumerate(values):check_data('bad_'+field+'_'+str(i),{field:v})
 # Syntax-only source identity: semantic match against Git is the build recipe's responsibility.
 check_data('zero_sha_syntax_only',{'source_commit':'0'*40},True)
 raw=json.dumps(base).encode(); record('valid_exact_4096',lambda:mod.parse(raw+b' '*(4096-len(raw))),True)
 bad_raw=[b'',b' '*4097,raw+b' '*(4097-len(raw)),b'null',b'[]',b'1',b'true',b'{}',raw+raw,b'\xff',b'\xef\xbb\xbf'+raw,raw+b'\0',b'{"x":1,}',raw.replace(b'"schema_version": 1',b'"schema_version": 1e0'),raw.replace(b'"schema_version": 1',b'"schema_version": NaN'),raw.replace(b'"schema_version": 1',b'"schema_version": Infinity'),raw.replace(b'"schema_version": 1',b'"schema_version": -Infinity')]
 for i,r in enumerate(bad_raw):record('bad_raw_'+str(i),lambda r=r:mod.parse(r),False)
 for i,r in enumerate((raw.decode(),bytearray(raw),None,1,{})):record('wrong_input_type_'+str(i),lambda r=r:mod.parse(r),False)
 escaped=raw[:-1]+b',"\\u0073ource_commit":"'+base['source_commit'].encode()+b'"}'; record('escaped_duplicate_key',lambda:mod.parse(escaped),False)
 def cli(label,input_file,output_file,success):
  proc=subprocess.run([sys.executable,'-I','-B',str(script),'--input',str(input_file),'--output',str(output_file)],capture_output=True,text=True,timeout=20)
  (out/(label+'.stdout')).write_text(proc.stdout); (out/(label+'.stderr')).write_text(proc.stderr); need((proc.returncode==0)==success,label+' exit'); results[label]={'pass':True,'accepted':success,'exit':proc.returncode}; return proc
 inp=out/'valid declaration.json'; inp.write_bytes(raw); generated=out/'generated.c'; proc=cli('cli_valid',inp,generated,True); receipt=json.loads(proc.stdout); need(receipt['status']=='DIAGNOSTIC_DECLARATION_ONLY' and receipt['input_sha256']==hashlib.sha256(raw).hexdigest(),'bad CLI receipt'); need(all(receipt[k] is False for k in ('transaction_bound_qualified','runtime_profile_accepted','hardware_verified','flash_approved')),'false qualification claim'); need(generated.read_text()==mod.generate(base),'CLI/API C mismatch')
 old=generated.read_bytes(); cli('cli_existing_output',inp,generated,False); need(generated.read_bytes()==old,'existing output overwritten'); cli('cli_same_input_output',inp,inp,False); need(inp.read_bytes()==raw,'input overwritten')
 directory=out/'existing-directory'; directory.mkdir(); cli('cli_directory_output',inp,directory,False)
 hard=out/'hard-link.c'; os.link(inp,hard); cli('cli_hard_link_output',inp,hard,False); need(inp.read_bytes()==raw,'hardlink input overwritten')
 link=out/'symlink.c'; link.symlink_to(inp); cli('cli_symlink_output',inp,link,False); need(inp.read_bytes()==raw,'symlink input overwritten')
 dangling=out/'dangling.c'; target=out/'absent-target'; dangling.symlink_to(target); cli('cli_dangling_symlink_output',inp,dangling,False); need(not target.exists(),'dangling target created')
 bad=out/'bad.json'; bad.write_text(json.dumps(dict(base,input_kind='qualified'))); cli('cli_invalid_no_output',bad,out/'must-not-exist.c',False); need(not (out/'must-not-exist.c').exists(),'invalid generated output')
 cli('cli_missing_input',out/'missing.json',out/'missing-output.c',False); need(not (out/'missing-output.c').exists(),'missing input generated output')
 need(script.read_bytes()==raw_script,'parser source changed'); result={'status':'PASS','cases':results,'case_count':len(results),'script_sha256':hashlib.sha256(raw_script).hexdigest(),'physical_verification':False,'source_identity_semantics':'syntax only; build recipe must bind exact source'}; (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps({'status':'PASS','cases':len(results),'receipt':str(out/'receipt.json')}))
if __name__=='__main__': main()
