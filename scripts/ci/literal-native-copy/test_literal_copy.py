"""Portable synthetic tests. No DLL is loaded and no Windows result is implied."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('literal_copy', HERE / 'literal_copy.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
# Tests require the separately published, hash-pinned observer source as data.
HELPER = Path(os.environ.get('LITERAL46_TEST_HELPER', str(HERE.parent / 'observe_native_origins.py')))

class Fixture:
    def __init__(self, root):
        self.root = root
        self.bundle = root / 'bundle'
        self.bundle.mkdir()
        (self.bundle / '_internal').mkdir()
        self.origins = root / 'origins'
        self.origins.mkdir()
        self.reference = json.loads((HERE / 'run18-reference.json').read_bytes())
        self.baseline = []
        self.toc = []
        self.mapping = {}
        self.rows = sorted(self.reference['native_rows'], key=lambda r:r['path'])
        for i, row in enumerate(self.rows):
            payload = ('fixture-body-%02d-' % i).encode() * 5
            row['bytes'], row['sha256'] = len(payload), m.sha(payload)
            (self.bundle / row['path']).write_bytes(payload)
            source = self.origins / ('leaf-%02d.dll' % i)
            source.write_bytes(payload)
            self.mapping[row['declared_source']] = source
            self.baseline.append({k:row[k] for k in ('path','bytes','sha256')})
            self.toc.append((row['path'][len('_internal/'):],row['declared_source'],'BINARY'))
        self.reference['native_rows'] = self.rows
        self.config = {k:str(root / k) for k in ('reference','baseline','current','toc','output')}
        self.config.update({'self':str(HERE / 'literal_copy.py'), 'helper':str(HELPER.resolve()),
                            'bundle':str(self.bundle)})
        Path(self.config['baseline']).write_text(json.dumps(self.baseline))
        self.base_hash = m.sha(Path(self.config['baseline']).read_bytes())
        self.reference['baseline_sha256'] = self.base_hash
        Path(self.config['reference']).write_text(json.dumps(self.reference))
        self.ref_hash = m.sha(Path(self.config['reference']).read_bytes())
        self.current = copy.deepcopy(self.reference)
        self.current['context'].update(run_id='999', attempt='1', workflow_sha='a'*40)
        self.config['expected'] = {k:self.current['context'][k] for k in ('run_id','attempt','workflow_sha')}
        self.save()

    def save(self):
        Path(self.config['toc']).write_text(repr((self.toc,)))
        self.current['input_hashes']['toc'] = m.sha(Path(self.config['toc']).read_bytes())
        Path(self.config['current']).write_text(json.dumps(self.current))

    def run(self):
        with patch.object(m,'REFERENCE_HASH',self.ref_hash), patch.object(m,'BASELINE_HASH',self.base_hash), \
             patch.object(m,'origin_path',lambda x:self.mapping[x]):
            return m.observe(self.config)

class LiteralTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='literal46-test-')
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name))

    def test_success_and_exact_accounting(self):
        opened=[]; counts={'read':0,'write':0}
        real_open,real_read,real_write=m.os.open,m.os.read,m.os.write
        def op(path,flags,*args):
            opened.append((str(path),flags));return real_open(path,flags,*args)
        def rd(fd,n):
            b=real_read(fd,n);counts['read']+=len(b);return b
        def wr(fd,b):
            n=real_write(fd,b);counts['write']+=n;return n
        with patch.object(m.os,'open',op),patch.object(m.os,'read',rd),patch.object(m.os,'write',wr):
            r=self.f.run()
        self.assertEqual(r['status'],'observed_literal_byte_equality')
        self.assertEqual(r['managed_path_count'],99)
        self.assertEqual((r['read_bytes'],r['write_bytes']),(counts['read'],counts['write']))
        self.assertEqual(r['managed_bytes'],sum(counts.values()))
        self.assertLessEqual(r['managed_bytes'],123*m.MIB)
        report=json.loads(Path(self.f.config['output']).read_bytes())
        self.assertEqual(report['status'],'UNCOMMITTED_LITERAL_OBSERVATION')
        self.assertEqual(m.sha(Path(self.f.config['output']).read_bytes()),r['report_sha256'])
        for k in m.CLEARANCE:self.assertIs(r[k],False);self.assertIs(report[k],False)
        first_origin=min(i for i,(p,_) in enumerate(opened) if Path(p).parent==self.f.origins)
        bundle_reads={p for p,_ in opened[:first_origin] if str(self.f.bundle) in p}
        self.assertEqual(len(bundle_reads),46)

    def bad_bundle(self,index):
        p=self.f.bundle/self.f.rows[index]['path'];p.write_bytes(b'Z'*len(p.read_bytes()))
        origins=[];real=m.os.open
        def spy(path,flags,*a):
            if Path(path).parent==self.f.origins:origins.append(str(path))
            return real(path,flags,*a)
        with patch.object(m.os,'open',spy),self.assertRaises(ValueError):self.f.run()
        self.assertEqual(origins,[])
        self.assertFalse(Path(self.f.config['output']).exists())

    def test_bad_first_bundle_zero_origins(self):self.bad_bundle(0)
    def test_bad_last_bundle_zero_origins(self):self.bad_bundle(-1)

    def test_declared_origin_substitution(self):
        self.f.current['native_rows'][-1]['declared_source']='C:\\elsewhere\\x.dll'
        self.f.save()
        with self.assertRaises(ValueError):self.f.run()
        self.assertFalse(Path(self.f.config['output']).exists())

    def test_wrong_current_event(self):
        self.f.current['context']['run_id']='998';self.f.save()
        with self.assertRaises(ValueError):self.f.run()

    def test_toc_substitution(self):
        a,b,c=self.f.toc[-1];self.f.toc[-1]=(a,b,'DATA');self.f.save()
        with self.assertRaises(ValueError):self.f.run()

    def test_prefixed_collect_destination_rejected(self):
        self.f.toc=[('_internal/'+a,b,c) for a,b,c in self.f.toc]
        self.f.save()
        with self.assertRaisesRegex(ValueError,'COLLECT'):self.f.run()

    def test_actual_pinned_reference_data_join(self):
        helper_raw=HELPER.read_bytes()
        self.assertEqual(m.sha(helper_raw),m.HELPER_HASH)
        namespace={'__name__':'_test_pinned_helper','__file__':str(HELPER)}
        exec(compile(helper_raw,str(HELPER),'exec'),namespace)
        import types
        helper=types.SimpleNamespace(**namespace)
        reference_raw=(HERE/'run18-reference.json').read_bytes()
        baseline_raw=(HELPER.parent/'run17-native-baseline.json').read_bytes()
        current=json.loads(reference_raw)
        current['context'].update(run_id='999',attempt='1',workflow_sha='a'*40)
        toc_raw=repr(([(r['path'][len('_internal/'):],r['declared_source'],'BINARY')
                       for r in current['native_rows']],)).encode()
        current['input_hashes']['toc']=m.sha(toc_raw)
        rows,context=m.bind(helper,reference_raw,baseline_raw,json.dumps(current).encode(),
                            toc_raw,self.f.config['expected'])
        self.assertEqual(len(rows),46)
        self.assertEqual(sum(r['bytes'] for r in rows),10850576)
        self.assertEqual(context['run_id'],'999')

    def test_current_toc_hash(self):
        Path(self.f.config['toc']).write_text(repr(([],)))
        with self.assertRaises(ValueError):self.f.run()

    def test_baseline_hash_drift(self):
        Path(self.f.config['baseline']).write_bytes(b'[]')
        with self.assertRaises(ValueError):self.f.run()

    def test_reference_hash_drift(self):
        Path(self.f.config['reference']).write_bytes(b'{}')
        with self.assertRaises(ValueError):self.f.run()

    def test_observer_helper_hash_drift(self):
        p=Path(self.temp.name)/'bad-helper';p.write_bytes(b'raise Exception("must not execute")')
        self.f.config['helper']=str(p)
        with self.assertRaisesRegex(ValueError,'helper pin'):self.f.run()

    def test_duplicate_native_rows(self):
        self.f.current['native_rows'][-1]=copy.deepcopy(self.f.current['native_rows'][0]);self.f.save()
        with self.assertRaises(ValueError):self.f.run()

    def test_distinct_path_handle_ctime_is_accepted(self):
        import types
        real=m.os.fstat
        def changed(fd):
            info=real(fd)
            values={k:getattr(info,k) for k in
                    ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')}
            values['st_ctime_ns']+=100000
            values['st_file_attributes']=getattr(info,'st_file_attributes',0)
            return types.SimpleNamespace(**values)
        with patch.object(m.os,'fstat',changed):r=self.f.run()
        self.assertEqual(r['status'],'observed_literal_byte_equality')

    def test_distinct_path_handle_mtime_is_rejected(self):
        import types
        real=m.os.fstat
        def changed(fd):
            info=real(fd)
            values={k:getattr(info,k) for k in
                    ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')}
            values['st_mtime_ns']+=500
            values['st_file_attributes']=getattr(info,'st_file_attributes',0)
            return types.SimpleNamespace(**values)
        with patch.object(m.os,'fstat',changed),self.assertRaisesRegex(ValueError,'identity'):
            self.f.run()

    def test_windows_superscript_devices_rejected(self):
        import ntpath
        from pathlib import PureWindowsPath
        for stem in ('COM¹','COM²','COM³','LPT¹','LPT²','LPT³','CONIN$','CONOUT$',
                     'CON ','COM1 ','COM¹ ','LPT¹ ','CONIN$ '):
            with self.subTest(stem=stem),patch.object(m,'Path',PureWindowsPath), \
                 patch.object(m.os,'name','nt'),patch.object(m.os.path,'normpath',ntpath.normpath):
                with self.assertRaisesRegex(ValueError,'Reserved device'):
                    m.canonical('C:\\fixed\\'+stem+'.txt')

    def output_handle_reparse(self,number):
        import types
        p=Path(self.temp.name)/'output-handle-control'
        reader=m.Reader({p:100});real=m.os.fstat;counter=[0]
        def fake(fd):
            info=real(fd);counter[0]+=1
            if counter[0]!=number:return info
            values={k:getattr(info,k) for k in
                    ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')}
            values['st_file_attributes']=0x400
            return types.SimpleNamespace(**values)
        with patch.object(m.os,'fstat',fake),self.assertRaises(ValueError):reader.write_report(p,b'report')
        self.assertTrue(p.exists())
        self.assertEqual(p.stat().st_size,0 if number==1 else 6)

    def test_opened_output_reparse_rejected_before_write(self):self.output_handle_reparse(1)
    def test_finished_output_reparse_rejected(self):self.output_handle_reparse(2)

    def test_boolean_schema_version_rejected(self):
        self.f.current['schema_version']=True;self.f.save()
        with self.assertRaises(ValueError):self.f.run()

    def handle_reparse(self,number):
        import types
        real=m.os.fstat;counter=[0]
        def fake(fd):
            original=real(fd);counter[0]+=1
            if counter[0]!=number:return original
            values={k:getattr(original,k) for k in
                    ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')}
            values['st_file_attributes']=0x400
            return types.SimpleNamespace(**values)
        with patch.object(m.os,'fstat',fake),self.assertRaises(ValueError):self.f.run()
        self.assertFalse(Path(self.f.config['output']).exists())

    def test_open_handle_reparse_rejected(self):self.handle_reparse(1)
    def test_final_handle_reparse_rejected(self):self.handle_reparse(2)

    def test_clearance_true_rejected(self):
        self.f.current['legal_clearance']=True;self.f.save()
        with self.assertRaises(ValueError):self.f.run()

    def test_absent_origin_is_incomplete(self):
        self.f.mapping[self.f.rows[0]['declared_source']].unlink()
        r=self.f.run();self.assertEqual(r['status'],'incomplete')
        rows=json.loads(Path(self.f.config['output']).read_bytes())['native_rows']
        self.assertEqual(rows[0]['source_copy']['status'],'absent')

    def test_different_origin_bytes(self):
        p=self.f.mapping[self.f.rows[0]['declared_source']];b=p.read_bytes();p.write_bytes(b'Z'*len(b))
        self.assertEqual(self.f.run()['status'],'incomplete')

    def test_origin_size_mismatch_not_opened(self):
        p=self.f.mapping[self.f.rows[0]['declared_source']];p.write_bytes(b'Z')
        real=m.os.open;seen=[]
        def spy(path,flags,*a):
            if Path(path)==p:seen.append(str(path))
            return real(path,flags,*a)
        with patch.object(m.os,'open',spy):r=self.f.run()
        self.assertEqual(r['status'],'incomplete');self.assertEqual(seen,[])

    def test_existing_output_preserved(self):
        p=Path(self.f.config['output']);p.write_bytes(b'keep')
        with self.assertRaises(ValueError):self.f.run()
        self.assertEqual(p.read_bytes(),b'keep')

    def test_output_inside_bundle_rejected(self):
        self.f.config['output']=str(self.f.bundle/'report.json')
        with self.assertRaises(ValueError):self.f.run()

    def test_symlink_bundle_leaf(self):
        p=self.f.bundle/self.f.rows[0]['path'];p.unlink();p.symlink_to(self.f.mapping[self.f.rows[0]['declared_source']])
        with self.assertRaises(ValueError):self.f.run()

    def test_symlink_origin_is_error_not_followed(self):
        p=self.f.mapping[self.f.rows[0]['declared_source']];p.unlink();p.symlink_to(self.f.bundle/self.f.rows[0]['path'])
        self.assertEqual(self.f.run()['status'],'incomplete')
        rows=json.loads(Path(self.f.config['output']).read_bytes())['native_rows']
        self.assertEqual(rows[0]['source_copy']['status'],'error')

    def test_mutation_during_output_has_no_success_receipt(self):
        real=m.Reader.write_report
        def mutate(reader,path,raw):
            real(reader,path,raw)
            (self.f.bundle/self.f.rows[-1]['path']).write_bytes(b'changed')
        with patch.object(m.Reader,'write_report',mutate),self.assertRaises(ValueError):self.f.run()
        report=json.loads(Path(self.f.config['output']).read_bytes())
        self.assertEqual(report['status'],'UNCOMMITTED_LITERAL_OBSERVATION')

    def test_budget_rejects_before_first_open(self):
        real=m.os.open;seen=[]
        def spy(*a,**kw):seen.append(a[0]);return real(*a,**kw)
        with patch.object(m,'MANAGED_LIMIT',1),patch.object(m.os,'open',spy),self.assertRaises(ValueError):self.f.run()
        self.assertEqual(seen,[])

    def test_final_reread_charged(self):
        r=self.f.run();Path(self.f.config['output']).unlink()
        with patch.object(m,'MANAGED_LIMIT',r['managed_bytes']-1),self.assertRaises(ValueError):self.f.run()

    def test_casefold_alias_plan(self):
        p=Path(self.temp.name)
        with self.assertRaises(ValueError):m.Reader({p/'A':10,p/'a':10})

    def test_path_100_rejected(self):
        p=Path(self.temp.name)
        with self.assertRaises(ValueError):m.Reader({p/('p%d'%i):1 for i in range(100)})

    def test_ads_and_traversal_rejected(self):
        for value in (str(Path(self.temp.name))+'/x:ads',str(Path(self.temp.name))+'/../x',str(Path(self.temp.name))+'/CON'):
            with self.subTest(value=value),self.assertRaises(ValueError):m.canonical(value)

    def test_no_production_cli_on_non_windows(self):
        if os.name=='nt':self.skipTest('non-Windows guard only')
        args=[]
        for k in ('bundle','helper','reference','baseline','current','toc','output'):
            args+=['--'+k,self.f.config[k]]
        args+=['--run-id','999','--attempt','1','--workflow-sha','a'*40]
        with self.assertRaisesRegex(ValueError,'Windows'):m.main(args)

if __name__=='__main__':unittest.main()
