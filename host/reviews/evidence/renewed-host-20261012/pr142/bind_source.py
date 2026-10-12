from pathlib import Path
import ast, hashlib, io, json, os, subprocess, tarfile

R = Path('/tmp/notice-supplement-critic')
E = R/'evidence'
repo = Path('/tmp/motortester-host-qt-notices')
head = '699363047fa923c128fd41da67326bc008acbcdb'
base = '3bc7a251cd36714b1fec350c99e0e5b43ffc4950'
tree = '03e334c06f3adabad704b5113b40a13f8b25ee36'
def git(*args):
    return subprocess.check_output(['git', '-C', str(repo), *args])
def entries(commit):
    result = {}
    for entry in filter(None, git('ls-tree', '-rz', '--full-tree', commit).split(b'\0')):
        meta, path = entry.split(b'\t', 1)
        mode, kind, blob = meta.decode().split()
        assert kind == 'blob' and mode in ('100644','100755')
        result[path.decode()] = {'mode': mode, 'git_blob': blob}
    return result
assert git('rev-parse', 'HEAD').decode().strip() == head
assert git('rev-parse', head+'^{tree}').decode().strip() == tree
assert git('show', '-s', '--format=%P', head).decode().strip() == base
assert git('status', '--porcelain', '--untracked-files=all') == b''
before, after = entries(base), entries(head)
expected = set(Path('/tmp/host-license-gap-audit/implementation-evidence/seven-paths.txt').read_text().splitlines())
changed = {p for p in before.keys()|after.keys() if before.get(p) != after.get(p)}
assert changed == expected and len(changed) == 7 and len(before) == 1387 and len(after) == 1391
assert not before.keys()-after.keys()
source = R/'source'
source.mkdir(exist_ok=False)
archive = git('archive', '--format=tar', head)
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    assert all(m.isdir() or m.isfile() for m in tar.getmembers())
    tar.extractall(source, filter='data')
freeze_path = Path('/tmp/host-license-gap-audit/implementation-evidence/SOURCE-FREEZE.json')
freeze_data = freeze_path.read_bytes()
(E/'author-SOURCE-FREEZE.json').write_bytes(freeze_data)
freeze = json.loads(freeze_data)
assert freeze['commit'] == head and freeze['tree'] == tree and freeze['base_commit'] == base
for p, rec in after.items():
    data = (source/p).read_bytes()
    assert (repo/p).read_bytes() == data
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == rec['git_blob'], p
    assert ('100755' if (source/p).stat().st_mode & 0o111 else '100644') == rec['mode'], p
    rec.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    assert rec == freeze['all_files'][p], p
for p, key in [('GPL-2.0-only.txt','GPL2'),('GPL-3.0-only.txt','GPL3'),('LGPL-3.0-only.txt','LGPL3')]:
    assert (source/'host/packaging/licenses/qt-6.12.0'/p).read_bytes() == (E/'upstream'/(key+'.txt')).read_bytes()
old = git('show', base+':host/packaging/collect_licenses.py').decode()
new = (source/'host/packaging/collect_licenses.py').read_text()
function = lambda code,name: ast.dump(next(n for n in ast.parse(code).body if isinstance(n,ast.FunctionDef) and n.name==name),include_attributes=False)
assert function(old, '_pyserial_notice') == function(new, '_pyserial_notice')
old_test = git('show', base+':host/tests/test_serial_packaging.py').decode()
new_test = (source/'host/tests/test_serial_packaging.py').read_text()
checks = lambda code: [ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(code)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr.startswith('assert')]
assert checks(old_test) == checks(new_test)
(E/'candidate.patch').write_bytes(git('diff', '--no-ext-diff', base, head))
(E/'candidate-commit.txt').write_bytes(git('cat-file','-p',head))
result = {'commit':head,'tree':tree,'sole_parent':base,'files':len(after),'unchanged_existing_files':len(before)-3,'changed_paths':sorted(changed),'source_hashes':after,'source_freeze_sha256':hashlib.sha256(freeze_data).hexdigest(),'pyserial_notice_function_ast_unchanged':True,'all_preexisting_serial_assertions_ast_unchanged':True,'recipe_blob':after['host/packaging/build_windows.py']['git_blob']}
assert result['recipe_blob'] == '503a9f4aec1cd7293770226caaaa5369c16b3cca'
(E/'SOURCE-BINDING.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='source_hashes'},indent=2))
