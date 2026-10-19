#!/usr/bin/env python3
"""Runner negative controls and relocation; modifies only a fresh output tree."""
import argparse, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
def require(ok,msg):
 if not ok: raise ValueError(msg)
def main():
 require(sys.flags.isolated and sys.dont_write_bytecode and not sys.flags.optimize,'run python3 -I -B without -O')
 ap=argparse.ArgumentParser(); ap.add_argument('--source',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args(); source,out=a.source,a.output
 require(source.is_absolute() and source.resolve()==source and source.is_dir(),'invalid source'); require(out.is_absolute() and out.resolve()==out and not out.exists() and out.parent.is_dir(),'output must be a fresh absolute directory'); require(not out.is_relative_to(source) and not out.is_relative_to(HERE),'output outside source/package required'); out.mkdir()
 results={}
 def run(label,argv,expected,needle=None):
  p=subprocess.run(list(map(str,argv)),cwd=out,capture_output=True,text=True,timeout=180,env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','TZ':'UTC'})
  (out/(label+'.stdout')).write_text(p.stdout); (out/(label+'.stderr')).write_text(p.stderr)
  require((p.returncode==0)==expected,label+' exit'); require(needle is None or needle in p.stderr,label+' expected diagnostic'); results[label]={'exit':p.returncode,'expected_success':expected,'pass':True}; (out/'controls.json').write_text(json.dumps(results,indent=2)+'\n')
 base=[sys.executable,'-I','-B',HERE/'run.py','--source',source]
 run('python_optimize_rejected',[sys.executable,'-I','-B','-O',HERE/'run.py','--source',source,'--output',out/'optimized'],False,'without -O')
 run('nonisolated_python_rejected',[sys.executable,'-B',HERE/'run.py','--source',source,'--output',out/'nonisolated'],False,'without -O')
 run('existing_output_rejected',base+['--output',out],False,'fresh external output')
 run('source_overlap_rejected',base+['--output',source/'forbidden-test-output'],False,'fresh external output')
 alias=out/'source-alias'; alias.symlink_to(source,target_is_directory=True)
 run('symlink_source_rejected',[sys.executable,'-I','-B',HERE/'run.py','--source',alias,'--output',out/'alias-result'],False,'nonsymlink path')
 staged=out/'source with spaces'; manifest=json.loads((HERE/'source-manifest.json').read_text());
 for rel in manifest['files']:
  p=staged/rel; p.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source/rel,p)
 changed=staged/'firmware/app/mt_live_runtime.c'; original=changed.read_bytes(); changed.write_bytes(original+b'\n/* negative control */\n')
 run('changed_source_rejected',[sys.executable,'-I','-B',HERE/'run.py','--source',staged,'--output',out/'changed-result'],False,'source pin mismatch'); changed.write_bytes(original)
 package=out/'package with spaces'; package.mkdir()
 for p in HERE.iterdir():
  if p.is_file(): shutil.copyfile(p,package/p.name)
 run('relocated_strict_and_sanitize',[sys.executable,'-I','-B',package/'run.py','--source',staged,'--output',out/'relocated result'],True)
 broken=out/'sanitizer fail-fast package'; shutil.copytree(package,broken); fixture=broken/'test_boot.c'; content=fixture.read_text(); target='int main(int argc,char **argv) {'; require(content.count(target)==1,'fixture main anchor')
 # --list must remain readable. The first sanitizer case must stop at the
 # deliberate signed overflow; strict mode retains the unmodified behavior.
 fault='''int main(int argc,char **argv) {
#if defined(__SANITIZE_ADDRESS__)
 if(argc==2 && strcmp(argv[1],"--list")) { volatile int32_t maximum=INT32_MAX; volatile int32_t overflow=maximum+1; (void)overflow; }
#endif
'''
 fixture.write_text(content.replace(target,fault))
 run('sanitizer_failure_is_fatal',[sys.executable,'-I','-B',broken/'run.py','--source',staged,'--output',out/'sanitizer failed result'],False,'sanitize-case-success_order_and_real_init failed')
 r=json.loads((out/'sanitizer failed result/receipt.json').read_text()); require(r['status']=='FAILED' and not r['cases']['sanitize'],'sanitizer continued after UB'); error=(out/'sanitizer failed result/sanitize-case-success_order_and_real_init.stderr').read_text(); require('runtime error: signed integer overflow' in error,'expected UBSan evidence')
 require(all(not (out/x).exists() for x in ('optimized','nonisolated','alias-result','changed-result')),'rejected runner created output'); require(not (source/'forbidden-test-output').exists(),'runner wrote into source')
 print(json.dumps({'status':'PASS','controls':len(results),'receipt':str(out/'controls.json')}))
if __name__=='__main__': main()
