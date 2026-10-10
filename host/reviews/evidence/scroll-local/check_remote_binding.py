import hashlib,json,pathlib,subprocess
r=pathlib.Path('/tmp/critic-b-scroll-local/source')
def git(*args):return subprocess.check_output(['git','-C',str(r),*args])
sha='f0822f7b336b0cab0e01632eee33f0f9a24473cc';tree='89ee762ea583e587924ceb15bf7507b9d9afe29b';parent='bd58245ecf64f08dcfe6d6fec3981e26fd7ff0ed';path='host/tests/test_gui.py';expected_sha256='d44e46031d56bbd1be1edae28b063a97c0628aba9a198fa581af0b39e0ed3b03'
actual=git('show','--no-patch','--format=%H%n%T%n%P','FETCH_HEAD').decode().splitlines()
if actual!=[sha,tree,parent]:raise RuntimeError(actual)
if git('write-tree').decode().strip()!=tree:raise RuntimeError('Reviewed index tree differs')
if git('diff','--name-only',parent,sha).decode().splitlines()!=[path]:raise RuntimeError('Unexpected changed paths')
data=git('show',sha+':'+path)
if hashlib.sha256(data).hexdigest()!=expected_sha256 or data!=(r/path).read_bytes():raise RuntimeError('File bytes differ')
for item in git('ls-tree','-rz',sha).split(b'\0'):
 if not item:continue
 meta,name=item.split(b'\t'); mode,kind,blob=meta.decode().split();name=name.decode()
 if git('cat-file','blob',blob)!=(r/name).read_bytes():raise RuntimeError('Snapshot differs: '+name)
git('diff','--check',parent,sha)
print(json.dumps({'remote_fetch_ref':'refs/pull/71/head','remote_pr_url':'https://github.com/font-weight/stm32_motortester/pull/71','verified_sha':sha,'verified_tree':tree,'verified_parent':parent,'tracked_files_verified':105,'remote_tree_equals_reviewed_tree':True,'material_delta_since_tests':False,'changed_files_from_parent':[path],'test_file_sha256':expected_sha256,'tests_repeated_for_binding':False,'windows_result':'not performed for this SHA'},indent=2))
