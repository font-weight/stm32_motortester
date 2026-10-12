"""Exercise root's corrected pre-mkdir separation guard without changing inputs."""
from pathlib import Path
import hashlib,json,subprocess,sys,tempfile
p=Path(__file__).resolve().parent
script=p/'snapshot/tests/usb-setup/check_patch.py'
roots=[Path('/workspace/shared/usb-dependencies'),Path('/workspace/shared/usb-cdc-tests'),Path('/workspace/shared/usb-adapter-draft'),p/'snapshot/firmware/third_party/usb/patches']
args=[sys.executable,str(script),'--dependencies',str(roots[0]),'--supporting',str(roots[1]),'--firmware',str(roots[2]),'--toolchain','/workspace/shared/arm-resource-evidence/toolchain/root/usr']
receipts=[]
with tempfile.TemporaryDirectory(dir=p,prefix='separation-aliases-') as tmp:
 for i,root in enumerate(roots):
  alias=Path(tmp)/str(i);alias.symlink_to(root,target_is_directory=True)
  for output in (root,root/'critic-usb003-must-not-be-created',alias/'critic-usb003-must-not-be-created'):
   if output!=root:assert not output.exists()
   before=set(root.iterdir())
   r=subprocess.run(args+['--output',str(output)],capture_output=True,text=True)
   assert r.returncode==2 and 'output must be outside every input tree' in r.stderr,(output,r)
   assert set(root.iterdir())==before
   if output!=root:assert not output.exists()
   receipts.append({'output':str(output),'exit_code':r.returncode,'stderr':r.stderr,'input_entries_unchanged':True})
(p/'output-separation-results.json').write_text(json.dumps({'runner_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'result':'PASS','cases':receipts},indent=2)+'\n')
print('PASS: 12 equal/nested/symlink-alias destinations rejected before creation; input directories unchanged')
