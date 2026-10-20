from pathlib import Path
import ast,hashlib,json,os,platform,subprocess,sys
R=Path('/tmp/motortester-axis-zoom');O=Path(__file__).resolve().parent
HEAD='b57cbdd356e0b3c68e2758a33fe069c51e87c7bb';TREE='062ef56c1fa13e3600a30873e273156bcd3fe921';BASE='3bc7a251cd36714b1fec350c99e0e5b43ffc4950'
def check(ok,msg):
 if not ok:raise RuntimeError(msg)
def git(*args):return subprocess.check_output(['git','-C',str(R),*args])
check(git('rev-parse','HEAD').decode().strip()==HEAD,'head');check(git('rev-parse','HEAD^{tree}').decode().strip()==TREE,'tree');check(git('rev-parse','HEAD^').decode().strip()==BASE,'parent')
changed=git('diff','--name-only',BASE,HEAD).decode().splitlines();check(changed==['host/docs/help.md','host/motortester/gui.py','host/tests/test_axis_zoom.py'],'scope')
path='host/motortester/gui.py';before=ast.parse(git('show',BASE+':'+path));after=ast.parse((R/path).read_text())
def mainclass(t):return next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name=='MainWindow')
a,b=mainclass(before),mainclass(after);a_methods={n.name:n for n in a.body if isinstance(n,ast.FunctionDef)};b_methods={n.name:n for n in b.body if isinstance(n,ast.FunctionDef)}
method_delta=[n for n in a_methods if ast.dump(a_methods[n])!=ast.dump(b_methods[n])];check(set(method_delta)=={'__init__','_build_ui','reset_view'},'methodscope')
# Remove reviewed methods before checking every other production statement.
for c in (a,b):c.body=[n for n in c.body if not(isinstance(n,ast.FunctionDef) and n.name in method_delta)]
check(ast.dump(before)==ast.dump(after),'other production statements')
entries=[];unchanged=0
old={}
for row in filter(None,git('ls-tree','-r','-z',BASE).split(b'\0')):
 meta,path=row.split(b'\t');old[path.decode()]=meta.decode()
for row in filter(None,git('ls-tree','-r','-z',HEAD).split(b'\0')):
 meta,path=row.split(b'\t');mode,typ,sha=meta.decode().split();path=path.decode();data=(R/path).read_bytes();check(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==sha,path)
 if path not in changed:check(old[path]==meta.decode(),'inheritance '+path);unchanged+=1
 entries.append({'path':path,'mode':mode,'blob':sha,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
check(not git('status','--porcelain').decode(),'dirtytree');git('diff','--check',BASE,HEAD)
obj={'local_head':HEAD,'tree':TREE,'parent':BASE,'changed_paths':changed,'verified_files':len(entries),'unchanged_files':unchanged,'changed_gui_methods':method_delta,'other_production_ast_unchanged':True,'clean':True,'entries':entries}
(O/'source-proof.json').write_text(json.dumps(obj,indent=2)+'\n');(O/'candidate.patch').write_bytes(git('diff',BASE,HEAD));print(json.dumps({k:v for k,v in obj.items() if k!='entries'},indent=2))
import PySide6,numpy,pyqtgraph,serial,motortester.gui,motortester.gui_acquisition,motortester.acquisition,motortester.runtime
mods=[motortester.gui,motortester.gui_acquisition,motortester.acquisition,motortester.runtime]
env={'python':sys.version,'platform':platform.platform(),'deps':{m.__name__:{'version':m.__version__,'file':m.__file__} for m in [PySide6,numpy,pyqtgraph,serial]},'modules':{m.__name__:m.__file__ for m in mods},'QT_QPA_PLATFORM':os.environ.get('QT_QPA_PLATFORM')}
(O/'environment.json').write_text(json.dumps(env,indent=2)+'\n')
