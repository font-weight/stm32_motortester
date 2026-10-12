"""Independent collector probes. Fixtures are not Windows or legal evidence."""
from pathlib import Path
from contextlib import ExitStack
from importlib import metadata
import builtins, hashlib, importlib.util, json, os, shutil, socket, subprocess, sys, unittest
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request

R=Path('/tmp/notice-supplement-critic'); E=R/'evidence'
mode='optimized' if sys.flags.optimize else 'normal'
O=E/('independent-replay-'+mode); O.mkdir(exist_ok=False)
SOURCE=R/'source'
def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
collector=module(SOURCE/'host/packaging/collect_licenses.py','independent_collector')
base_path=O/'base-collector.py'
base_path.write_bytes(subprocess.check_output(['git','-C','/tmp/motortester-host-qt-notices','show','3bc7a251cd36714b1fec350c99e0e5b43ffc4950:host/packaging/collect_licenses.py']))
base=module(base_path,'baseline_collector')
upstream=json.loads((E/'UPSTREAM-VERIFIED.json').read_text())
EXPECTED={x['name']:x for x in upstream['notices']}
WHEELS={'pyside6-essentials':('pyside6_essentials-6.12.0-cp310-abi3-win_amd64.whl','c9a95102aa23c1f86516a30d5a9be37552d324eee7e567eb7508973f1218cb42'),
        'shiboken6':('shiboken6-6.12.0-cp310-abi3-win_amd64.whl','a1906cb8116869178c64b6bab52623bbc8b4ceed7b30bfc3c9aa0f6e67f6438e')}
records=[]
class Probe(unittest.TestCase):
    def fixture(self,label):
        root=O/(label+' тест'); root.mkdir()
        runtime=root/'python runtime';runtime.mkdir();(runtime/'LICENSE.txt').write_bytes(b'fixture CPython text\n')
        installed=root/'installed';installed.mkdir();(installed/'LicenseRef-Qt-Commercial.txt').write_bytes(b'fixture conditional commercial reference\n')
        def dist(name,version):
            return SimpleNamespace(metadata={'Name':name,'License-Expression':'fixture'},version=version,files=['LicenseRef-Qt-Commercial.txt'],locate_file=lambda x:installed/x)
        d=[dist('pyserial','3.5'),dist('PySide6_Essentials','6.12.0'),dist('shiboken6','6.12.0')]
        return root,runtime,d,dist
    def collect(self,root,runtime,d,mod=collector,**patches):
        target=root/'output лицензии'
        def denied(*args,**kwargs):raise AssertionError('unexpected network/subprocess')
        original_import=builtins.__import__
        def no_qt(name,*args,**kwargs):
            if name.split('.')[0] in ('PySide6','shiboken6'):raise AssertionError('unexpected Qt runtime import')
            return original_import(name,*args,**kwargs)
        with ExitStack() as stack:
            stack.enter_context(patch.object(mod.metadata,'distributions',return_value=iter(d)))
            stack.enter_context(patch.object(mod,'sys',SimpleNamespace(base_prefix=str(runtime))))
            stack.enter_context(patch.object(mod,'PYSERIAL_LICENSE',SOURCE/'host/packaging/licenses/pyserial-3.5-LICENSE.txt'))
            for name,value in patches.items():stack.enter_context(patch.object(mod,name,value))
            for obj,name in [(socket,'socket'),(socket,'create_connection'),(urllib.request,'urlopen'),(subprocess,'Popen')]:stack.enter_context(patch.object(obj,name,side_effect=denied))
            stack.enter_context(patch.object(builtins,'__import__',side_effect=no_qt))
            mod.collect(target)
        return json.loads((target/'inventory.json').read_text()),target
    def positive(self,label,changes=None,actual=False):
        root,runtime,d,dist=self.fixture(label)
        if changes:changes(d)
        if actual:
            d=list(metadata.distributions(path=['/workspace/shared/motortester-runtime-recovery-20261012/deps']))
            self.assertEqual(len(d),6)
        inv,target=self.collect(root,runtime,d)
        self.assertEqual((target/'CPython-LICENSE.txt').read_bytes(),(runtime/'LICENSE.txt').read_bytes())
        qt=[]
        for record in inv:
            self.assertIs(record['manual_review_required'],True)
            norm=collector._distribution_name(record['name'])
            if norm not in WHEELS:continue
            qt.append(norm);self.assertEqual(len(record['vendored_notices']),3)
            for n in record['vendored_notices']:
                filename=Path(n['path']).name;expected=EXPECTED[filename]
                self.assertEqual(n['path'],'qt-6.12.0/'+filename)
                self.assertIn(n['path'],record['copied_notices'])
                self.assertEqual((target/n['path']).read_bytes(),(SOURCE/'host/packaging/licenses/qt-6.12.0'/filename).read_bytes())
                self.assertEqual(n['sha256'],expected['sha256']);self.assertEqual(n['source_git_blob'],expected['git_blob']);self.assertEqual(n['bytes'],expected['bytes'])
                self.assertEqual(n['source_commit'],'f50facb83fc3dc5f3240c854668c7a6dd93871b2')
                self.assertEqual(n['source_url'],f"https://raw.githubusercontent.com/pyside/pyside-setup/{n['source_commit']}/LICENSES/{filename}")
                self.assertEqual((n['reference_wheel'],n['reference_wheel_sha256']),WHEELS[norm])
                self.assertEqual(n['version'],'6.12.0');self.assertEqual(n['scope'],'upstream-license-text-only')
            if not actual:
                self.assertTrue(any('LicenseRef-Qt-Commercial.txt' in name for name in record['copied_notices']))
            else:
                # Restored Linux metadata has no discovered notice entries.
                # It cannot establish the Windows wheel's retained notice set.
                distribution=next(x for x in d if x.metadata['Name']==record['name'])
                discovered=[str(f) for f in distribution.files or () if any(word in str(f).lower() for word in ('license','licence','copying','notice','copyright'))]
                self.assertEqual(discovered,[])
        self.assertEqual(sorted(qt),sorted(WHEELS))
        self.assertEqual({p.name for p in (target/'qt-6.12.0').iterdir()},set(EXPECTED))
        serial=next(r for r in inv if r['name']=='pyserial')
        self.assertEqual((target/serial['vendored_notices'][0]['path']).read_bytes(),(SOURCE/'host/packaging/licenses/pyserial-3.5-LICENSE.txt').read_bytes())
        records.append({'case':label,'result':'PASS','kind':'real Linux installed metadata/notices plus fixture CPython' if actual else 'fake distribution source check','inventory_sha256':hashlib.sha256((target/'inventory.json').read_bytes()).hexdigest()})
    def failure(self,label,mutate=None,patches=None):
        root,runtime,d,dist=self.fixture(label)
        kw=patches(root) if patches else {}
        if mutate:mutate(root,runtime,d,dist)
        with self.assertRaises((RuntimeError,OSError)) as caught:self.collect(root,runtime,d,**kw)
        self.assertFalse((root/'output лицензии/inventory.json').exists())
        records.append({'case':label,'result':'rejected','error':type(caught.exception).__name__+': '+str(caught.exception)})
    def test_01_success_and_baseline_control(self):
        self.positive('happy')
        self.positive('normalized',lambda d:(d[1].metadata.update(Name='PYSIDE6..__Essentials'),d[2].metadata.update(Name='SHIBOKEN6')))
        self.positive('installed-linux-inputs',actual=True)
        root,runtime,d,dist=self.fixture('baseline-gap')
        inv,target=self.collect(root,runtime,d,base)
        self.assertFalse((target/'qt-6.12.0').exists())
        self.assertTrue(all(not r['vendored_notices'] for r in inv if r['name']!='pyserial'))
        records.append({'case':'baseline-gap','result':'old collector completes while omitting all three GNU texts','kind':'fresh negative control'})
    def test_02_distribution_guards(self):
        for index in [1,2]:
            self.failure(f'missing-qt-{index}',lambda r,p,d,f,i=index:d.pop(i))
            for ordinal,version in enumerate(['6.12.1','6.12','6.12.0+local',None]):
                self.failure(f'version-{index}-{ordinal}',lambda r,p,d,f,i=index,v=version:setattr(d[i],'version',v))
            alias='PYSIDE6._-ESSENTIALS' if index==1 else 'SHIBOKEN6'
            self.failure(f'duplicate-{index}',lambda r,p,d,f,a=alias:d.append(f(a,'6.12.0')))
    def test_03_each_missing_or_changed_notice(self):
        for index,filename in enumerate(EXPECTED):
            for kind in ['missing','same-size','extra-byte']:
                def patches(root,n=filename,k=kind):
                    vendor=root/'altered texts';shutil.copytree(SOURCE/'host/packaging/licenses/qt-6.12.0',vendor)
                    p=vendor/n
                    if k=='missing':p.unlink()
                    else:
                        raw=p.read_bytes();p.write_bytes(bytes([raw[0]^1])+raw[1:] if k=='same-size' else raw+b'X')
                    return {'QT_LICENSES':vendor}
                self.failure(f'text-{index}-{kind}',patches=patches)
    def test_04_inherited_guard_and_existing_output(self):
        self.failure('pyserial-absent',lambda r,p,d,f:d.pop(0))
        self.failure('pyserial-version',lambda r,p,d,f:setattr(d[0],'version','3.4'))
        for kind in ['missing','modified']:
            def patches(root,k=kind):
                p=root/'pyserial-license.txt'
                if k=='modified':p.write_bytes(b'not the upstream text')
                return {'PYSERIAL_LICENSE':p}
            self.failure('pyserial-'+kind,patches=patches)
        self.failure('cpython-absent',lambda r,p,d,f:(p/'LICENSE.txt').unlink())
        for kind in ['file','directory','symlink']:
            root=O/('preexisting-'+kind);root.mkdir();target=root/'out'
            if kind=='file':target.write_bytes(b'sentinel')
            elif kind=='directory':target.mkdir();(target/'inventory.json').write_bytes(b'sentinel')
            else:
                destination=root/'real';destination.mkdir();(destination/'inventory.json').write_bytes(b'sentinel');target.symlink_to(destination,target_is_directory=True)
            with patch.object(collector.metadata,'distributions') as read,self.assertRaises(FileExistsError):collector.collect(target)
            read.assert_not_called()
            self.assertEqual((target if kind=='file' else target/'inventory.json').read_bytes(),b'sentinel')
            records.append({'case':'preexisting-'+kind,'result':'rejected, original bytes preserved'})
    def test_05_partial_copy_fails_without_inventory(self):
        root,runtime,d,dist=self.fixture('late-copy-fault')
        original=Path.open
        def fail(path,*args,**kwargs):
            if path==root/'output лицензии/qt-6.12.0/LGPL-3.0-only.txt':raise PermissionError('independent injected copy failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',fail),self.assertRaises(PermissionError):self.collect(root,runtime,d)
        self.assertTrue((root/'output лицензии/qt-6.12.0/GPL-2.0-only.txt').exists())
        self.assertFalse((root/'output лицензии/inventory.json').exists())
        records.append({'case':'late-copy-fault','result':'partial output retained, no complete inventory'})

result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Probe))
(O/'RESULTS.json').write_text(json.dumps({'optimized':bool(sys.flags.optimize),'groups':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'outcomes':records},indent=2)+'\n')
sys.exit(not result.wasSuccessful())
