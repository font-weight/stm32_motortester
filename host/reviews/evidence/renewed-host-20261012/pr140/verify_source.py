from pathlib import Path
import ast,collections,hashlib,json,os,platform,subprocess,sys
R=Path('/workspace/shared/motortester-host-wrap-controls-20261012');O=Path(__file__).resolve().parent
HEAD='3a718eda6587b623b6ea3d06fd293f2068a351b5';TREE='131f11ec4ee21d5c281cae220baebee03906724b';BASE='3bc7a251cd36714b1fec350c99e0e5b43ffc4950'
def check(ok,msg):
 if not ok:raise RuntimeError(msg)
def git(*args):return subprocess.check_output(['git','-C',str(R),*args])
check(git('rev-parse','HEAD').decode().strip()==HEAD,'head');check(git('rev-parse','HEAD^{tree}').decode().strip()==TREE,'tree');check(git('rev-parse','HEAD^').decode().strip()==BASE,'parent')
changed=git('diff','--name-only',BASE,HEAD).decode().splitlines();check(changed==['host/docs/acquisition-integration.md','host/motortester/gui_acquisition.py','host/tests/test_gui_acquisition.py'],'scope')
path='host/motortester/gui_acquisition.py';before=ast.parse(git('show',BASE+':'+path));after=ast.parse((R/path).read_text())
new_texts={'Подключить ·\nбез запуска захвата','Подтверждаю\nфизический сброс','Событие запуска\nв эмуляторе'}
class Unwrap(ast.NodeTransformer):
 def visit_Constant(self,node):
  if isinstance(node.value,str) and node.value in new_texts:node.value=node.value.replace('\n',' ')
  return node
check(ast.dump(before)==ast.dump(Unwrap().visit(after)),'production delta exceeds 3 text line breaks')
path='host/tests/test_gui_acquisition.py';old=ast.parse(git('show',BASE+':'+path));new=ast.parse((R/path).read_text())
def methods(tree):
 c=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AcquisitionGuiTests');return {n.name:n for n in c.body if isinstance(n,ast.FunctionDef)}
a,b=methods(old),methods(new)
for name,node in a.items():
 if name!='assert_panel_reachable':check(ast.dump(node)==ast.dump(b[name]),'existing test changed '+name)
def asserts(node):return collections.Counter(ast.dump(n) for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr.startswith('assert'))
oldasserts=asserts(a['assert_panel_reachable']);newasserts=asserts(b['assert_panel_reachable']);check(all(newasserts[n]>=count for n,count in oldasserts.items()),'assertion weakened')
entries=[]
for row in filter(None,git('ls-tree','-r','-z',HEAD).split(b'\0')):
 meta,path=row.split(b'\t');mode,typ,sha=meta.decode().split();path=path.decode();data=(R/path).read_bytes();check(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==sha,path)
 entries.append({'path':path,'mode':mode,'blob':sha,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
check(not git('status','--porcelain').decode(),'dirty tree');git('diff','--check',BASE,HEAD)
obj={'local_head':HEAD,'tree':TREE,'parent':BASE,'changed_paths':changed,'verified_files':len(entries),'unchanged_files':len(entries)-3,'production_ast_identical_except_three_linebreaks':True,'original_test_methods_unchanged':len(a)-1,'original_helper_assertions_preserved':sum(oldasserts.values()),'added_helper_assertions':sum(newasserts.values())-sum(oldasserts.values()),'new_tests':sorted(set(b)-set(a)),'clean':True,'entries':entries}
(O/'source-proof.json').write_text(json.dumps(obj,indent=2)+'\n');(O/'candidate.patch').write_bytes(git('diff',BASE,HEAD));print(json.dumps({k:v for k,v in obj.items() if k!='entries'},indent=2))
import PySide6,numpy,pyqtgraph,serial,motortester.gui,motortester.gui_acquisition,motortester.acquisition,motortester.runtime
mods=[motortester.gui,motortester.gui_acquisition,motortester.acquisition,motortester.runtime]
env={'python':sys.version,'platform':platform.platform(),'deps':{m.__name__:{'version':m.__version__,'file':m.__file__} for m in [PySide6,numpy,pyqtgraph,serial]},'modules':{m.__name__:m.__file__ for m in mods},'QT_QPA_PLATFORM':os.environ.get('QT_QPA_PLATFORM')}
(O/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
