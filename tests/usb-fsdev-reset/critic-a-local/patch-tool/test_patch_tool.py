"""Independent fail-closed checks of the exact patch applicator.
Only temporary test fixtures are created/modified; upstream is read-only.
"""
import hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
INPUT=ROOT/'inputs'
LOG=[]

def digest(b): return hashlib.sha256(b).hexdigest()
def execute(tool,source,output,ok=True,code=None):
    argv=[sys.executable,str(tool),str(source),str(output)]
    if code is not None:argv=[sys.executable,'-c',code,str(tool),str(source),str(output)]
    r=subprocess.run(argv,capture_output=True,text=True)
    LOG.append({'argv':argv,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
    assert (r.returncode==0)==ok,LOG[-1]
    return r

with tempfile.TemporaryDirectory(prefix='critic-usb-setup-',dir=ROOT) as work:
    w=Path(work);tool=w/'apply_f103_reset_patch.py'
    for name in ['apply_f103_reset_patch.py','f103-reset-cleanup.json','0002-f103-reset-completion-cleanup.patch']:
        shutil.copyfile(INPUT/name,w/name)
    source=w/'upstream source.c';original=(INPUT/'upstream-usbd.c').read_bytes();source.write_bytes(original)
    manifest=json.loads((w/'f103-reset-cleanup.json').read_text())
    assert digest(original)==manifest['source_sha256']
    out=w/'new output.c';execute(tool,source,out)
    expected=out.read_bytes();assert digest(expected)==manifest['result_sha256']
    assert source.read_bytes()==original
    execute(tool,source,out,False);assert out.read_bytes()==expected
    execute(tool,source,source,False);assert source.read_bytes()==original
    existing=w/'unrelated.txt';existing.write_text('keep sentinel')
    linked=w/'output-symlink';linked.symlink_to(existing)
    execute(tool,source,linked,False);assert existing.read_text()=='keep sentinel' and linked.is_symlink()
    dangling=w/'dangling-output';target=w/'absent-target';dangling.symlink_to(target)
    execute(tool,source,dangling,False);assert dangling.is_symlink() and not target.exists()
    directory=w/'existing-directory';directory.mkdir();execute(tool,source,directory,False)
    assert directory.is_dir() and not list(directory.iterdir())
    bad=w/'bad-source.c';bad.write_bytes(original+b'\n')
    no_output=w/'rejected-output';execute(tool,bad,no_output,False);assert not no_output.exists()
    patch=w/'0002-f103-reset-completion-cleanup.patch';patch_bytes=patch.read_bytes()
    patch.write_bytes(patch_bytes+b'\n');execute(tool,source,no_output,False);assert not no_output.exists()
    patch.write_bytes(patch_bytes)
    original_manifest=(w/'f103-reset-cleanup.json').read_bytes()
    wrong=dict(manifest,result_sha256='0'*64)
    (w/'f103-reset-cleanup.json').write_text(json.dumps(wrong))
    execute(tool,source,no_output,False);assert not no_output.exists()
    (w/'f103-reset-cleanup.json').write_bytes(original_manifest)
    # Inject another writer exactly after real GNU patch returns, before xb.
    # Tool source stays unchanged; this tests its explicit exclusive-create claim.
    race_output=w/'raced-output'
    code='''import subprocess,sys,runpy\nfrom pathlib import Path\nscript,source,out=sys.argv[1:]\nreal=subprocess.run\ndef during(*a,**kw):\n r=real(*a,**kw)\n Path(out).write_text("concurrent sentinel")\n return r\nsubprocess.run=during\nsys.argv=[script,source,out]\nrunpy.run_path(script,run_name="__main__")\n'''
    execute(tool,source,race_output,False,code)
    assert race_output.read_text()=='concurrent sentinel'
    assert source.read_bytes()==original and patch.read_bytes()==patch_bytes
    assert (w/'f103-reset-cleanup.json').read_bytes()==original_manifest
    # A missing output directory must not leave any partial output/source edit.
    absent=w/'uncreated'/'result.c';execute(tool,source,absent,False)
    assert not absent.exists() and source.read_bytes()==original
    (ROOT/'patched-usbd.c').write_bytes(expected)
result={'result':'PASS','invocations':len(LOG),'source_sha256':digest(original),'result_sha256':digest(expected),'scope':'Patch applicator filesystem/hash behavior only; no TinyUSB lifecycle or hardware claim','commands':LOG}
(ROOT/'patch-tool-results.json').write_text(json.dumps(result,indent=2)+'\n')
print('PASS:',len(LOG),'patch-tool invocations; original/source/patch/manifests preserved; exclusive output race rejected')
