from pathlib import Path
import hashlib,json,subprocess
O=Path(__file__).resolve().parent;R=Path('/tmp/motortester-axis-zoom')
def check(v,msg):
 if not v:raise RuntimeError(msg)
def load(name):return json.loads((O/name).read_text())
c,p,t,pr,ref,local=[load(n) for n in ['remote-commit.json','remote-parent.json','remote-tree.json','remote-pr.json','remote-ref.json','source-proof.json']]
HEAD='da469ceaa6421de40357376b859ad5175d6d4984';BASE='3bc7a251cd36714b1fec350c99e0e5b43ffc4950'
check(c['sha']==HEAD and c['tree']['sha']==local['tree'] and [x['sha'] for x in c['parents']]==[BASE],'commit');check(p['tree']['sha']=='d80d0d730a193480004382857375f5a0f6c65515','parent');check(pr['head']['sha']==HEAD and pr['base']['sha']==BASE and ref['object']['sha']==HEAD,'refs');check(not t['truncated'],'truncated')
a={}
for row in filter(None,subprocess.check_output(['git','-C',str(R),'ls-tree','-r','-t','-z','HEAD']).split(b'\0')):
 meta,path=row.split(b'\t');mode,typ,sha=meta.decode().split();path=path.decode();a[path]={'mode':mode,'type':typ,'sha':sha}
 if typ=='blob':a[path]['size']=(R/path).stat().st_size
b={x['path']:{k:x[k] for k in ['mode','type','sha','size'] if k in x} for x in t['tree']};check(a==b,'treeentries')
hashes={}
for i,path in enumerate(local['changed_paths']):
 raw=(O/f'remote-body-{i}.txt').read_bytes();check(raw==(R/path).read_bytes(),'body'+path);hashes[path]=hashlib.sha256(raw).hexdigest()
obj={'remote_head':HEAD,'tree':local['tree'],'remote_parent':BASE,'local_head':local['local_head'],'bound_entries':len(a),'bound_files':local['verified_files'],'actual_remote_bodies_sha256':hashes,'draft':pr['draft'],'exact_source_binding':True};(O/'remote-verification.json').write_text(json.dumps(obj,indent=2)+'\n');print(json.dumps(obj,indent=2))
