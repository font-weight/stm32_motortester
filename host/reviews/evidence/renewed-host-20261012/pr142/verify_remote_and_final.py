from pathlib import Path
import hashlib, json, subprocess, zipfile

R=Path('/tmp/notice-supplement-critic'); E=R/'evidence'; remote=E/'remote'
proof=json.loads((E/'SOURCE-BINDING.json').read_text())
sha=lambda data:hashlib.sha256(data).hexdigest()
head='24eadd15530a84384f58f9edcb50ddfea8785796'
commit=json.loads((remote/'commit.json').read_text())
assert commit['sha']==head and commit['tree']['sha']==proof['tree']
assert [x['sha'] for x in commit['parents']]==[proof['sole_parent']]
pr=json.loads((remote/'pr.json').read_text())
assert pr['number']==142 and pr['draft'] is True and pr['state']=='open'
assert pr['head']['sha']==head and pr['base']['sha']==proof['sole_parent'] and pr['changed_files']==7
assert pr['head']['ref']=='dot-b/host-qt-notice-supplement' and pr['base']['ref']=='dot-b/host-viewport-diagnostics'
for name,expected in [('head-ref',head),('base-ref',proof['sole_parent'])]:
    assert json.loads((remote/(name+'.json')).read_text())['object']['sha']==expected
tree=json.loads((remote/'tree.json').read_text())
assert not tree['truncated'] and tree['sha']==proof['tree']
blobs={x['path']:{'mode':x['mode'],'git_blob':x['sha']} for x in tree['tree'] if x['type']=='blob'}
assert blobs=={p:{k:r[k] for k in ['mode','git_blob']} for p,r in proof['source_hashes'].items()}
local_entries=subprocess.check_output(['git','-C','/tmp/motortester-host-qt-notices','ls-tree','-rzt',proof['commit']])
all_entries={}
for e in filter(None,local_entries.split(b'\0')):
    meta,path=e.split(b'\t',1);mode,kind,blob=meta.decode().split();all_entries[path.decode()]=(mode,kind,blob)
assert all_entries=={x['path']:(x['mode'],x['type'],x['sha']) for x in tree['tree']}
for path in proof['changed_paths']:
    data=(remote/'bodies'/path).read_bytes()
    assert data==(R/'source'/path).read_bytes()
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==blobs[path]['git_blob']
for root in [R/'source',Path('/tmp/motortester-host-qt-notices')]:
    for path,entry in proof['source_hashes'].items():
        assert sha((root/path).read_bytes())==entry['sha256'],path
        assert ('100755' if (root/path).stat().st_mode & 0o111 else '100644')==entry['mode'],path
assert subprocess.check_output(['git','-C','/tmp/motortester-host-qt-notices','status','--porcelain','--untracked-files=all'])==b''
archive=E/'source-local699363.zip'
subprocess.run(['git','-C','/tmp/motortester-host-qt-notices','archive','--format=zip','--output='+str(archive),proof['commit'],'host'],cwd=R/'source/host',check=True)
expected={p:r for p,r in proof['source_hashes'].items() if p.startswith('host/')}
with zipfile.ZipFile(archive) as z:
    files={i.filename:i for i in z.infolist() if not i.is_dir()}
    assert set(files)==set(expected) and z.comment==proof['commit'].encode() and z.testzip() is None
    for name in files:assert sha(z.read(name))==expected[name]['sha256'],name
    members=len(z.infolist())
focused=json.loads((E/'FOCUSED-RESULTS.json').read_text())
assert all(sum(int(r['tests'][0][0]) for r in runs)==81 and all(r['exit']==0 and r['zero_skips'] for r in runs) for runs in focused.values())
controls={}
for mode in ['normal','optimized']:
    r=json.loads((E/('independent-replay-'+mode)/'RESULTS.json').read_text())
    assert r['groups']==5 and r['failures']==r['errors']==r['skips']==0
    controls[mode]=len(r['outcomes'])
result={'status':'PASS bounded source notice supplementation only','remote_commit':head,'local_commit':proof['commit'],'tree':proof['tree'],'sole_parent':proof['sole_parent'],'remote_tree_entries':len(all_entries),'remote_files':len(blobs),'actual_delta_bodies':7,'unchanged_source_files_after_review':len(blobs),'focused_tests':{'normal':81,'optimized':81},'independent_outcomes':controls,'local_source_archive':{'files':len(files),'members':members,'sha256':sha(archive.read_bytes()),'comment':proof['commit'],'remote_zip_identity_claimed':False},'windows_execution_performed':False,'legal_clearance':False,'full_composition_suite_performed':False}
(E/'FINAL-BINDING.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
