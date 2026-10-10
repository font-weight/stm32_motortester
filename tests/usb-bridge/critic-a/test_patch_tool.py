#!/usr/bin/env python3
"""Exact USB005 applicator checks. Mutations affect disposable copies only."""
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--patches',type=Path,required=True);p.add_argument('--core',type=Path,required=True);p.add_argument('--fsdev',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
out=a.out.resolve()
for root in (a.patches.resolve(),a.core.resolve().parent,a.fsdev.resolve().parent,Path(__file__).resolve().parent):
 if out==root or root in out.parents:p.error('output must be outside test/input directories')
original={x:hashlib.sha256(x.read_bytes()).hexdigest() for x in list(a.patches.glob('*'))+[a.core,a.fsdev] if x.is_file()}
out.mkdir(parents=True,exist_ok=False);logs=[]
def run(tool,kind,src,dst,ok=False,code=None):
 cmd=[sys.executable,str(tool),kind,str(src),str(dst)]
 if code: cmd=[sys.executable,'-c',code,str(tool),kind,str(src),str(dst)]
 r=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 logs.append(dict(argv=cmd,exit_status=r.returncode,output=r.stdout))
 assert (r.returncode==0)==ok,logs[-1]
for kind,source_input in (('core',a.core),('fsdev',a.fsdev)):
 root=out/kind;root.mkdir();patches=root/'patches';shutil.copytree(a.patches,patches)
 srcdir=root/'source';srcdir.mkdir();src=srcdir/'input.c';original_bytes=source_input.read_bytes();src.write_bytes(original_bytes)
 tool=patches/'apply_usb_bridge_patch.py';m_path=patches/'usb005-patches.json';mb=m_path.read_bytes();manifest=json.loads(mb);item=manifest['inputs'][kind]
 pp=patches/item['patch'];pb=pp.read_bytes();good=root/'good.c';run(tool,kind,src,good,True);expected=good.read_bytes();assert hashlib.sha256(expected).hexdigest()==item['result_sha256']
 run(tool,kind,src,good);assert good.read_bytes()==expected
 run(tool,kind,src,src);assert src.read_bytes()==original_bytes
 for dst in (srcdir/'nested.c',patches/'nested.c'):
  run(tool,kind,src,dst);assert not dst.exists()
 link=root/'alias';link.symlink_to(srcdir,target_is_directory=True);run(tool,kind,src,link/'nested.c');assert not (srcdir/'nested.c').exists()
 sentinel=root/'sentinel';sentinel.write_text('keep');linkfile=root/'outlink';linkfile.symlink_to(sentinel);run(tool,kind,src,linkfile);assert sentinel.read_text()=='keep' and linkfile.is_symlink()
 dangling=root/'dangling';missing=root/'missing';dangling.symlink_to(missing);run(tool,kind,src,dangling);assert not missing.exists()
 directory=root/'directory';directory.mkdir();run(tool,kind,src,directory);assert not list(directory.iterdir())
 bad=root/'bad.c';bad.write_bytes(original_bytes+b'\n');dst=root/'rejected.c';run(tool,kind,bad,dst);assert not dst.exists()
 pp.write_bytes(pb+b'\n');run(tool,kind,src,dst);assert not dst.exists();pp.write_bytes(pb)
 manifest['inputs'][kind]['result_sha256']='0'*64;m_path.write_text(json.dumps(manifest));run(tool,kind,src,dst);assert not dst.exists();m_path.write_bytes(mb)
 code='''import subprocess,sys,runpy\nfrom pathlib import Path\nscript,kind,source,out=sys.argv[1:]\nreal=subprocess.run\ndef during(*a,**kw):\n r=real(*a,**kw)\n Path(out).write_text("concurrent sentinel")\n return r\nsubprocess.run=during\nsys.argv=[script,kind,source,out]\nrunpy.run_path(script,run_name="__main__")\n'''
 race=root/'race';run(tool,kind,src,race,code=code);assert race.read_text()=='concurrent sentinel'
 missingparent=root/'absent'/'output.c';run(tool,kind,src,missingparent);assert not missingparent.exists()
 assert src.read_bytes()==original_bytes and pp.read_bytes()==pb and m_path.read_bytes()==mb
for x,d in original.items():assert hashlib.sha256(x.read_bytes()).hexdigest()==d,x
(out/'receipt.json').write_text(json.dumps(dict(result='PASS',invocations=len(logs),original_inputs={str(k):v for k,v in original.items()},commands=logs),indent=2)+'\n')
print(f'PASS: {len(logs)} applicator invocations; original inputs unchanged')
