import ast,hashlib,json,pathlib,subprocess
repo=pathlib.Path('/tmp/motortester-host-scroll');source=pathlib.Path('/tmp/critic-b-scroll-local/source')
parent='bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed';target='host/tests/test_gui.py'
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args])
def entries(root,ref):
 out={}
 for line in git(root,'ls-tree','-rz',ref).split(b'\0'):
  if not line:continue
  meta,name=line.split(b'\t',1);mode,kind,blob=meta.decode().split();out[name.decode()]={'mode':mode,'kind':kind,'blob':blob}
 return out
before=entries(repo,parent);tree=git(source,'write-tree').decode().strip();after=entries(source,tree)
if before.keys()!=after.keys():raise RuntimeError('Snapshot file set differs')
changed=[p for p in before if before[p]!=after[p]]
if changed!=[target]:raise RuntimeError(changed)
orig=ast.parse(git(repo,'show',f'{parent}:{target}'));new=ast.parse((source/target).read_bytes())
def methods(module):return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(module) if isinstance(n,ast.FunctionDef)}
m1,m2=methods(orig),methods(new)
changed_methods=[name for name in m1 if m1[name]!=m2[name]];added=sorted(m2.keys()-m1.keys())
if changed_methods!=['test_minimum_window_scrolls_annotations_with_larger_font_metrics']:raise RuntimeError(changed_methods)
if added!=['test_annotation_taller_than_viewport_is_fully_reachable_by_scrolling']:raise RuntimeError(added)
# Every tracked worktree file must be exactly the independent archive snapshot.
for p in before:
 if (repo/p).read_bytes()!=(source/p).read_bytes():raise RuntimeError(f'Worktree changed: {p}')
print(json.dumps({'parent':parent,'parent_tree':git(repo,'rev-parse',parent+'^{tree}').decode().strip(),'candidate_tree':tree,'commit':None,'remote_binding':'pending','tracked_files':len(before),'unchanged_files':len(before)-1,'changed_file':target,'git_blob':after[target]['blob'],'sha256':hashlib.sha256((source/target).read_bytes()).hexdigest(),'changed_methods':changed_methods,'added_methods':added,'production_unchanged':True,'files':after},indent=2))
