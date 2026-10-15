"""Opaque source-observation fixtures. No candidate/native code execution."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=Path(os.environ.get('SOURCE_COLLECTOR_INPUT_ROOT',str(HERE.parents[3])))
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
s=load('source_tested',HERE/'source_inputs.py')
f=load('prelaunch_fixtures',ROOT/'prelaunch-rebuild-20261015/scripts/ci/replacement-prelaunch/test_prelaunch.py')

def put(path,raw):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

class Fixture:
    def __init__(self,parent,variant='control'):
        self.base=parent;self.pre=f.Fixture(parent);self.variant=variant
        self.roots={name:parent/('source-'+name) for name in ('app','site','stdlib')}
        for root in self.roots.values():root.mkdir()
        appmap={}
        with zipfile.ZipFile(f.SOURCE) as archive:
            for entry in archive.infolist():
                if not entry.is_dir() and entry.filename.startswith('host/') and entry.filename.endswith('.py'):
                    raw=archive.read(entry);rel=entry.filename[5:]
                    appmap[rel]={**f.p.byte_record(raw),'owner':f.c.APP}
            rel='packaging/serial_import_smoke.py';raw=archive.read('host/'+rel)
            put(self.roots['app']/rel,raw)
        self.other=self.roots['app']/rel;self.target=self.roots['site']/'PySide6/__init__.py'
        original=f.ORIGINAL
        modified,_=f.c.modification(original,f.TRIAL)
        put(self.target,original if variant=='control' else modified)
        self.pool={'schema_version':1,'scope':'wrapper-frozen-source-pools','trial_id':f.TRIAL,
            'application':{'commit':f.c.APP,'tree':f.c.TREE},'roots':{k:str(v) for k,v in self.roots.items()},
            'files':{'app':appmap,'site':{'PySide6/__init__.py':{**f.p.byte_record(original),'owner':'PySide6-Essentials'}},'stdlib':{}}}
        base=self.pre.root/variant
        self.toc=parent/'metadata/PYZ-00.toc';self.pool_path=parent/'metadata/pool.json'
        self.work=base/'PYZ-00.pyz'
        self.rows=[('PySide6',str(self.target),'PYMODULE'),('unchanged',str(self.other),'PYMODULE')]
        self.config={'self':HERE/'source_inputs.py','prelaunch':f.HERE/'prelaunch.py','inspector':f.INSP,
            'companion':f.COMP,'pins':f.HERE/'pins.json','source_zip':f.SOURCE,
            'preparation':self.pre.root/'preparation','pool_manifest':self.pool_path,
            **self.roots,'exe':base/'bundle/Motortester.exe','work_pyz':self.work,'pure_toc':self.toc,
            'variant':variant,'source_output':parent/'results/source-inputs.json',
            'sidecar_output':parent/'results/sidecar.json'}
        (parent/'results').mkdir();self.save()
    def save(self):
        raw=f.c.encoded(self.pool);put(self.pool_path,raw);self.config['pool_sha256']=s.digest(raw)
        put(self.toc,repr((str(self.work),self.rows)).encode())
    def run(self):return s.collect(self.config)

class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='source-observation-');self.addCleanup(temp.cleanup)
        self.fixture=Fixture(Path(temp.name))
    def reject(self):
        try:self.fixture.run()
        except Exception as error:
            self.assertIn(type(error).__name__,('Rejected','Invalid'))
        else:self.fail('invalid source observation accepted')
        self.assertFalse(self.fixture.config['source_output'].exists())
        self.assertFalse(self.fixture.config['sidecar_output'].exists())
    def test_control(self):
        out=self.fixture.run();self.assertEqual(out['status'],'SOURCE_BYTES_OBSERVED')
        table=json.loads(self.fixture.config['source_output'].read_bytes())
        self.assertEqual(table['PySide6'],f.p.byte_record(f.ORIGINAL))
        for field in s.FALSE_FIELDS:self.assertIs(out[field],False)
    def test_modified(self):
        self.fixture.variant='modified';self.fixture.config['variant']='modified'
        base=self.fixture.pre.root/'modified'
        self.fixture.config['exe']=base/'bundle/Motortester.exe'
        self.fixture.work=base/'PYZ-00.pyz';self.fixture.config['work_pyz']=self.fixture.work
        self.fixture.save()
        modified,_=f.c.modification(f.ORIGINAL,f.TRIAL);put(self.fixture.target,modified)
        out=self.fixture.run();self.assertEqual(out['status'],'SOURCE_BYTES_OBSERVED')
    def test_wrong_hash(self):self.fixture.config['pool_sha256']='a'*64;self.reject()
    def test_changed_source(self):
        raw=bytearray(self.fixture.other.read_bytes());raw[0]^=1;put(self.fixture.other,raw);self.reject()
    def test_wrong_root_binding(self):self.fixture.pool['roots']['site']=str(self.fixture.base/'elsewhere');self.fixture.save();self.reject()
    def test_wrong_owner(self):self.fixture.pool['files']['site']['PySide6/__init__.py']['owner']='unknown';self.fixture.save();self.reject()
    def test_extra_schema_key(self):self.fixture.pool['extra']=False;self.fixture.save();self.reject()
    def test_missing_source(self):self.fixture.rows.pop();self.fixture.save();self.reject()
    def test_last_bad_source_zero_source_opens(self):
        self.fixture.rows[-1]=('unchanged',str(self.fixture.base/'outside.py'),'PYMODULE');self.fixture.save()
        actual=os.open;opened=[]
        def observed(path,*args,**kwargs):
            if Path(path) in (self.fixture.target,self.fixture.other):opened.append(str(path))
            return actual(path,*args,**kwargs)
        with mock.patch('os.open',observed):self.reject()
        self.assertEqual(opened,[])
    def test_symlink_source(self):
        target=self.fixture.other;backup=target.with_suffix('.saved');target.rename(backup);target.symlink_to(backup.name);self.reject()
    def test_pyc_source(self):self.fixture.rows[-1]=('unchanged',str(self.fixture.other.with_suffix('.pyc')),'PYMODULE');self.fixture.save();self.reject()
    def test_unsafe_toc_expression(self):put(self.fixture.toc,b"__import__('os').getcwd()");self.reject()
    def test_work_header_alias(self):put(self.fixture.toc,repr((str(self.fixture.work.parent/'..'/self.fixture.work.parent.name/self.fixture.work.name),self.fixture.rows)).encode());self.reject()
    def test_wrong_namespace(self):self.fixture.rows[-1]=('unchanged',None,'PYMODULE');self.fixture.save();self.reject()
    def test_declared_only_not_opened(self):
        self.fixture.rows.append(('not_embedded','Z:/unapproved/secret.py','PYMODULE'));self.fixture.save()
        self.fixture.run();sidecar=json.loads(self.fixture.config['sidecar_output'].read_bytes())
        self.assertEqual(sidecar['declared_only_names'],['not_embedded'])
    def test_budget(self):
        with mock.patch.object(s,'LIMIT',1024):self.reject()
    def test_app_map_forged(self):
        self.fixture.pool['files']['app']['packaging/serial_import_smoke.py']['sha256']='0'*64;self.fixture.save();self.reject()
    def test_original_target_manifest_forged(self):
        self.fixture.pool['files']['site']['PySide6/__init__.py']['bytes']+=1;self.fixture.save();self.reject()
    def test_output_existing_preserved(self):
        path=self.fixture.config['source_output'];put(path,b'keep')
        with self.assertRaises(Exception):self.fixture.run()
        self.assertEqual(path.read_bytes(),b'keep')
    def test_outputs_alias(self):self.fixture.config['sidecar_output']=self.fixture.config['source_output'];self.reject()
    def test_output_in_source_pool(self):
        self.fixture.config['source_output']=self.fixture.roots['site']/'new.py';self.reject()
    def test_ancestor_swap_before_recheck(self):
        original=s.bootstrap
        def wrapped(path):
            tool,raw,dirs=original(path);prior=tool.Inputs.recheck
            def changed(inputs):
                victim=self.fixture.target.parent;replacement=self.fixture.base/'replacement'
                replacement.mkdir();os.link(self.fixture.target,replacement/'__init__.py')
                victim.rename(self.fixture.base/'previous-site-package');replacement.rename(victim)
                return prior(inputs)
            tool.Inputs.recheck=changed
            return tool,raw,dirs
        with mock.patch.object(s,'bootstrap',wrapped):self.reject()
    def test_source_changed_at_recheck(self):
        original=s.bootstrap
        def wrapped(path):
            tool,raw,dirs=original(path);prior=tool.Inputs.recheck
            def changed(inputs):
                # Preparation reads do not call recheck; this is after source observation.
                body=bytearray(self.fixture.other.read_bytes());body[0]^=1;put(self.fixture.other,body)
                return prior(inputs)
            tool.Inputs.recheck=changed
            return tool,raw,dirs
        with mock.patch.object(s,'bootstrap',wrapped):self.reject()
    def test_stdlib_observed_source(self):
        path=self.fixture.roots['stdlib']/'example.py';raw=b'observed stdlib fixture'
        put(path,raw);self.fixture.pool['files']['stdlib']['example.py']={**f.p.byte_record(raw),'owner':'trusted-runtime-observed'}
        self.fixture.rows[-1]=('unchanged',str(path),'PYMODULE');self.fixture.save();self.fixture.run()
    def test_stdlib_nested_environment(self):
        path=self.fixture.roots['stdlib']/'nested/example.py';raw=b'bad nested source'
        put(path,raw);put(path.parent/'pyvenv.cfg',b'home = example')
        self.fixture.pool['files']['stdlib']['nested/example.py']={**f.p.byte_record(raw),'owner':'trusted-runtime-observed'}
        self.fixture.rows[-1]=('unchanged',str(path),'PYMODULE');self.fixture.save();self.reject()
    def test_table_pool_type_bool(self):
        self.fixture.pool['files']['site']['PySide6/__init__.py']['bytes']=True;self.fixture.save();self.reject()
    def test_sidecar_failure_never_success(self):
        original=os.open
        def broken(path,*args,**kwargs):
            if Path(path)==self.fixture.config['sidecar_output']:raise OSError('modeled output error')
            return original(path,*args,**kwargs)
        with mock.patch('os.open',broken),self.assertRaises(OSError):self.fixture.run()
        self.assertTrue(self.fixture.config['source_output'].exists())
        self.assertFalse(self.fixture.config['sidecar_output'].exists())
    def test_target_namespace_rejected(self):
        import marshal,struct
        payload=f.pyz();off=struct.unpack('!i',payload[8:12])[0]
        rows=f.t.TocData(payload[off:]).parse();rows[0]=('PySide6',(3,17,0))
        payload=payload[:off]+marshal.dumps(rows,4)
        put(self.fixture.config['exe'],f.exe(payload));self.fixture.rows[0]=('PySide6',None,'PYMODULE')
        self.fixture.save();self.reject()
    def test_same_byte_replacement_after_plan(self):
        original=s.bootstrap
        def wrapped(path):
            tool,raw,dirs=original(path);prior=tool.Inputs.read;changed=False
            def swapped(inputs,path,cap=65536,keep=True):
                nonlocal changed
                if Path(path)==self.fixture.other and not changed:
                    changed=True;body=Path(path).read_bytes();Path(path).rename(self.fixture.base/'previous-source')
                    put(Path(path),body)
                return prior(inputs,path,cap,keep)
            tool.Inputs.read=swapped;return tool,raw,dirs
        with mock.patch.object(s,'bootstrap',wrapped):self.reject()
    def test_late_pyvenv_appearance(self):
        path=self.fixture.roots['stdlib']/'example.py';body=b'actual stdlib fixture'
        put(path,body);self.fixture.pool['files']['stdlib']['example.py']={**f.p.byte_record(body),'owner':'trusted-runtime-observed'}
        self.fixture.rows[-1]=('unchanged',str(path),'PYMODULE');self.fixture.save()
        original=s.bootstrap
        def wrapped(helper):
            tool,raw,dirs=original(helper);prior=tool.Inputs.read;changed=False
            def mutated(inputs,leaf,cap=65536,keep=True):
                nonlocal changed
                if Path(leaf)==path and not changed:
                    changed=True;put(path.parent/'pyvenv.cfg',b'new nested environment')
                return prior(inputs,leaf,cap,keep)
            tool.Inputs.read=mutated;return tool,raw,dirs
        with mock.patch.object(s,'bootstrap',wrapped):self.reject()
    def test_mutation_during_terminal_readback(self):
        original=s.bootstrap
        def wrapped(helper):
            tool,raw,dirs=original(helper);prior=tool.Inputs.read;reads=0
            def mutated(inputs,path,cap=65536,keep=True):
                nonlocal reads
                result=prior(inputs,path,cap,keep)
                if Path(path)==self.fixture.config['sidecar_output']:
                    reads+=1
                    if reads==2:
                        body=bytearray(self.fixture.other.read_bytes());body[0]^=1;put(self.fixture.other,body)
                return result
            tool.Inputs.read=mutated;return tool,raw,dirs
        with mock.patch.object(s,'bootstrap',wrapped),self.assertRaises(Exception):self.fixture.run()
        sidecar=json.loads(self.fixture.config['sidecar_output'].read_bytes())
        self.assertEqual(sidecar['status'],'UNCOMMITTED_SOURCE_OBSERVATION')
    def test_bootstrap_reparse_handle(self):
        import types
        prior=os.fstat
        def redirected(fd):
            value=prior(fd)
            fields={key:getattr(value,key) for key in dir(value) if key.startswith('st_')}
            fields['st_file_attributes']=0x400
            return types.SimpleNamespace(**fields)
        with mock.patch('os.fstat',redirected):self.reject()
    def test_existing_output_case_alias(self):
        target=self.fixture.config['source_output'];alias=target.with_name(target.name.upper())
        put(alias,b'keep');self.reject();self.assertEqual(alias.read_bytes(),b'keep')
    def test_sidecar_inside_trial(self):
        self.fixture.config['sidecar_output']=self.fixture.pre.root/'control/extra.json';self.reject()
    def test_wrong_source_output_inside_trial(self):
        self.fixture.config['source_output']=self.fixture.pre.root/'modified/wrong.json';self.reject()
    def test_exact_closed_variant_output(self):
        path=self.fixture.pre.root/'control/source-inputs.json';path.unlink()
        self.fixture.config['source_output']=path;self.fixture.run()
    def test_wrong_exe_layout(self):
        path=self.fixture.base/'loose.exe';put(path,self.fixture.config['exe'].read_bytes())
        self.fixture.config['exe']=path;self.reject()
    def test_wrong_preparation_layout(self):
        self.fixture.config['preparation']=self.fixture.pre.root/'other-preparation';self.reject()
    def test_no_candidate_execution(self):
        with mock.patch('subprocess.Popen',side_effect=AssertionError('native launch')) as launch, \
             mock.patch('marshal.loads',side_effect=AssertionError('candidate load')) as unmarshaler:
            self.fixture.run();launch.assert_not_called();unmarshaler.assert_not_called()

if __name__=='__main__':unittest.main()
