"""Synthetic TOC and metadata fixtures; never historical execution evidence."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
ROOT=Path(os.environ['LITERAL_GATE_INPUT_ROOT']).resolve()
spec=importlib.util.spec_from_file_location('literal_gate_tested',HERE/'literal_gate.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture_json(value):
    return (json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"))+"\n").encode("utf-8")

def put(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

class Fixture:
    def __init__(self,root):
        self.root=root;root.mkdir(parents=True,exist_ok=True)
        self.config={'runner_temp':str(root),'run_id':'999','attempt':'1','workflow_sha':'a'*40,
          'self':str(HERE/'literal_gate.py'),'pins':str(HERE/'pins.json'),
          'workflow':str(ROOT/'checkout/.github/workflows/host-windows.yml'),
          'literal':str(ROOT/'checkout/scripts/ci/literal-native-copy/literal_copy.py'),
          'observer':str(ROOT/'checkout/scripts/ci/observe_native_origins.py'),
          'baseline':str(ROOT/'checkout/scripts/ci/run17-native-baseline.json'),
          'reference':str(ROOT/'checkout/scripts/ci/literal-native-copy/run18-reference.json')}
        self.build=root/'motortester-999-1';self.out=root/'literal46-999-1'
        self.current=json.loads(Path(self.config['reference']).read_bytes())
        self.current['context'].update(run_id='999',attempt='1',workflow_sha='a'*40)
        for role in ('source','copy'):
            self.current['cpython_notice'][role]['retention']['file']='native-notice-'+role+'-999-1.bin'
        d=ROOT/'metadata'
        self.manifest=json.loads((d/'build-manifest.json').read_bytes())
        self.provenance=json.loads((d/'provenance.json').read_bytes())
        self.archive=json.loads((d/'archive-verification.json').read_bytes())
        toc=[(row['path'][len('_internal/'):],row['declared_source'],'BINARY') for row in self.current['native_rows']]
        self.toc_path=self.build/'pyinstaller/motortester/COLLECT-00.toc'
        put(self.toc_path,(repr((toc,))+'\n# SYNTHETIC TOC: not the missing historical original.\n').encode())
        for name in ('SHA256SUMS.txt','frozen-recording.json','frozen-serial-import.json','frozen-window.png.smoke.json'):
            put(self.build/name,(d/name).read_bytes())
        for role in ('source','copy'):
            put(root/('native-notice-'+role+'-999-1.bin'),(d/('native-notice-'+role+'.bin')).read_bytes())
        self.current_path=root/'native-observation-999-1.json'
        self.save()

    def save(self):
        raw=fixture_json(self.manifest);put(self.build/'dist/Motortester/build-manifest.json',raw)
        self.provenance['build_manifest_sha256']=m.sha(raw)
        self.archive['build_manifest_sha256']=m.sha(raw)
        self.provenance['collect_toc_sha256']=m.sha(self.toc_path.read_bytes())
        put(self.root/'provenance-999-1.json',fixture_json(self.provenance))
        self.archive['checksum_file_sha256']=m.sha((self.build/'SHA256SUMS.txt').read_bytes())
        put(self.build/'archive-verification.json',fixture_json(self.archive))
        self.current['input_hashes']={'manifest':m.sha(raw),'provenance':m.sha(fixture_json(self.provenance)),
              'archive':m.sha(fixture_json(self.archive)),'toc':m.sha(self.toc_path.read_bytes())}
        put(self.current_path,fixture_json(self.current))

    def gate(self):
        return m.Gate(self.config)

    def run(self):
        self.g=self.gate();self.g.retain_current();self.g.check_joins();return self.g.finalize()

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='auxiliary-synthetic-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.f=Fixture(self.base/'runner')

    def retained(self):
        raw=(self.f.out/'current.original.bin').read_bytes()
        identity=json.loads((self.f.out/'current.identity.json').read_bytes())
        self.assertEqual(identity['original_sha256'],m.sha(raw))
        self.assertEqual(identity['original_bytes'],len(raw))
        return raw

    def reject(self):
        try:self.f.run()
        except Exception as error:
            self.assertIn(type(error).__name__,('Rejected','Invalid','FileNotFoundError',
                                               'JSONDecodeError','RuntimeError'))
        else:self.fail('invalid metadata accepted')
        self.retained()

    def test_positive_is_data_only(self):
        result=self.f.run();self.assertEqual(result['status'],'data_only_joins_validated')
        for key in m.FALSE_FIELDS:self.assertIs(result[key],False)
        self.assertEqual(result['planned_auxiliary_leaves'],26)
        self.assertLess(result['managed_bytes_before_terminal'],m.LIMIT-m.FAILURE_RESERVE)
        self.assertEqual(self.retained(),self.f.current_path.read_bytes())
        self.assertEqual(len(json.loads((self.f.out/'package-gate.json').read_bytes())['inputs']),18)
        allowed={str(v[0]) for v in self.f.g.io.allowed.values()}
        self.assertTrue(set(self.f.g.io.opens)<=allowed)

    def test_missing_later_input_preserves_original(self):
        (self.f.build/'dist/Motortester/build-manifest.json').unlink();self.reject()

    def test_malformed_current_retained_before_parse(self):
        put(self.f.current_path,b'{not-json');self.reject()
        self.assertEqual(self.retained(),b'{not-json')

    def test_bad_later_source_pin_retains(self):
        p=self.base/'bad-helper.py';put(p,b'raise RuntimeError("must never execute")')
        self.f.config['literal']=str(p);self.reject()

    def test_historical_toc_hash_not_fabricated(self):
        self.f.current['input_hashes']['toc']='83a8dcdc91d8c56a15d7b42365bfbd197c7001b7c59f41fbf335d0a60133dc37'
        put(self.f.current_path,fixture_json(self.f.current));self.reject()

    def test_current_extra_key(self):
        self.f.current['invented']=True;self.f.save();self.reject()

    def test_current_missing_hash(self):
        del self.f.current['input_hashes']['manifest'];put(self.f.current_path,fixture_json(self.f.current));self.reject()

    def test_current_wrong_scope(self):
        self.f.current['scope']='release approved';self.f.save();self.reject()

    def test_current_clearance(self):
        self.f.current['legal_clearance']=True;self.f.save();self.reject()

    def test_boolean_schema_version(self):
        self.f.current['schema_version']=True;self.f.save();self.reject()

    def test_native_extra_field(self):
        self.f.current['native_rows'][0]['invented']=1;self.f.save();self.reject()

    def test_native_duplicate(self):
        self.f.current['native_rows'][-1]=copy.deepcopy(self.f.current['native_rows'][0]);self.f.save();self.reject()

    def test_wrong_context(self):
        self.f.current['context']['run_id']='1000';self.f.save();self.reject()

    def test_wrong_inspector(self):
        self.f.provenance['inspector_commit']='b'*40;self.f.save();self.reject()

    def test_manifest_extra_key(self):
        self.f.manifest['extra']=0;self.f.save();self.reject()

    def test_provenance_extra_key(self):
        self.f.provenance['extra']=0;self.f.save();self.reject()

    def test_impossible_observer_counter(self):
        self.f.current['read_bytes']=1<<80;self.f.save();self.reject()

    def test_duplicate_json_key(self):
        raw=self.f.current_path.read_bytes();put(self.f.current_path,b'{"schema_version":1,'+raw[1:]);self.reject()

    def test_archive_delivered_claim(self):
        self.f.archive['bytes_delivered']=True;self.f.save();self.reject()

    def test_wrong_native_manifest_hash(self):
        path=self.f.current['native_rows'][0]['path'];self.f.manifest['files'][path]='0'*64
        self.f.save();self.reject()

    def test_authoritative_map_not_mutual_assertion(self):
        source=self.f.manifest['recording_smoke']['source_files'];key=next(iter(source));source[key]='0'*64
        self.f.archive['expected_inspector_recording']['source_files'][key]='0'*64
        self.f.provenance['recording_smoke']['source_files'][key]='0'*64
        self.f.save();self.reject()

    def test_checksum_extra_entry(self):
        p=self.f.build/'SHA256SUMS.txt';put(p,p.read_bytes()+b'0'*64+b'  extra\n')
        self.f.save();self.reject()

    def test_receipt_byte_drift(self):
        p=self.f.build/'frozen-serial-import.json';put(p,p.read_bytes()+b' ');self.reject()

    def test_notice_drift(self):
        p=self.f.root/'native-notice-copy-999-1.bin';put(p,p.read_bytes()+b'x');self.reject()

    def test_bad_source_alias_after_retention(self):
        self.f.config['literal']=self.f.config['observer'];self.reject()

    def test_no_clobber(self):
        self.f.out.mkdir();put(self.f.out/'keep',b'keep')
        with self.assertRaises(OSError):self.f.run()
        self.assertEqual((self.f.out/'keep').read_bytes(),b'keep')

    def test_symlink_later_input(self):
        target=self.f.build/'frozen-recording.json';raw=target.read_bytes();target.unlink()
        other=self.base/'recording';put(other,raw);target.symlink_to(other);self.reject()

    def test_partial_late_write_preserves_identity(self):
        gate=self.f.gate();gate.retain_current();gate.check_joins();before=self.retained()
        actual=os.write;calls=[0]
        def damaged(fd,raw):
            calls[0]+=1
            if calls[0]==1:return actual(fd,raw[:23])
            raise OSError('injected later write failure')
        with mock.patch.object(os,'write',damaged),self.assertRaises(OSError):gate.finalize()
        self.assertEqual((self.f.out/'package-gate.json').stat().st_size,23)
        self.assertEqual(self.retained(),before)
        failure=gate.failure(OSError('injected later write failure'))
        self.assertIs(failure['full_original_hash_available'],True)

    def test_terminal_source_mutation(self):
        gate=self.f.gate();gate.retain_current();gate.check_joins();original=gate.io.read;calls=[0]
        def read(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==gate.output/'package-gate.json':
                calls[0]+=1
                if calls[0]==2:put(self.f.current_path,self.f.current_path.read_bytes()+b' ')
            return result
        with mock.patch.object(gate.io,'read',read),self.assertRaises(m.Rejected):gate.finalize()
        self.retained()

    def test_terminal_output_alias(self):
        gate=self.f.gate();gate.retain_current();gate.check_joins();original=gate.io.read;calls=[0]
        def read(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==gate.output/'package-gate.json':
                calls[0]+=1
                if calls[0]==2:put(gate.output/'PACKAGE-GATE.JSON',b'late')
            return result
        with mock.patch.object(gate.io,'read',read),self.assertRaises(m.Rejected):gate.finalize()

    def test_budget_retention_survives_later_failure(self):
        gate=self.f.gate();gate.retain_current();gate.io.read_bytes=m.LIMIT-m.FAILURE_RESERVE-gate.io.write_bytes
        with self.assertRaises(m.Rejected):gate.check_joins()
        self.assertIs(gate.failure(m.Rejected('budget'))['full_original_hash_available'],True)
        self.assertLess(gate.io.total,m.LIMIT)

    def test_reader_exact_cap_and_accounting(self):
        p=self.base/'one';put(p,b'x');io=m.IO();io.extend({p:1})
        self.assertEqual(io.read(p),b'x');self.assertEqual(io.read(p),b'x');self.assertEqual(io.total,2)
        put(p,b'xx')
        with self.assertRaises(m.Rejected):io.read(p)

    def test_aggregate_one_byte_boundary(self):
        p=self.base/'one';put(p,b'x');io=m.IO();io.extend({p:1})
        io.read_bytes=m.LIMIT-m.FAILURE_RESERVE-1;self.assertEqual(io.read(p),b'x')
        with self.assertRaises(m.Rejected):io.read(p)

    def test_cross_api_mtime_rejected(self):
        import types
        actual=os.fstat
        def changed(fd):
            value=actual(fd);values={k:getattr(value,k) for k in dir(value) if k.startswith('st_')}
            values['st_mtime_ns']+=500;return types.SimpleNamespace(**values)
        with mock.patch.object(os,'fstat',changed),self.assertRaises(m.Rejected):self.f.run()

    def test_cross_api_ctime_qualified(self):
        import types
        actual=os.fstat
        def changed(fd):
            value=actual(fd);values={k:getattr(value,k) for k in dir(value) if k.startswith('st_')}
            values['st_ctime_ns']+=500;return types.SimpleNamespace(**values)
        with mock.patch.object(os,'fstat',changed):
            self.assertEqual(self.f.run()['status'],'data_only_joins_validated')

    def cli(self):
        command=[sys.executable,'-I','-S','-B']+(['-O'] if sys.flags.optimize else [])+[str(HERE/'literal_gate.py')]
        for key,value in self.f.config.items():
            if key not in ('self','pins'):command.extend(['--'+key.replace('_','-'),value])
        return subprocess.run(command,capture_output=True,timeout=30)

    def test_cli_success_charges_terminal_bytes(self):
        out=self.cli();self.assertEqual(out.returncode,0,out.stderr)
        receipt=json.loads(out.stdout)
        self.assertEqual(receipt['terminal_bytes'],len(out.stdout))
        self.assertEqual(receipt['managed_bytes'],receipt['managed_bytes_before_terminal']+len(out.stdout))
        self.assertEqual(receipt['status'],'data_only_joins_validated')
        for field in m.FALSE_FIELDS:self.assertIs(receipt[field],False)

    def test_cli_failure_retains_original(self):
        put(self.f.current_path,b'{bad')
        out=self.cli();self.assertEqual(out.returncode,1);self.assertEqual(out.stdout,b'')
        receipt=json.loads(out.stderr);self.assertEqual(receipt['status'],'AUXILIARY_REJECTED')
        self.assertIs(receipt['retention_completed'],True)
        self.assertEqual(receipt['observed_original'],m.record(b'{bad'))
        self.assertEqual(self.retained(),b'{bad')

    def test_cli_oversize_original_not_claimed_retained(self):
        put(self.f.current_path,b'x'*(128*1024+1))
        out=self.cli();self.assertEqual(out.returncode,1);receipt=json.loads(out.stderr)
        self.assertIs(receipt['retention_completed'],False)
        self.assertIs(receipt['full_original_hash_available'],False)
        self.assertIsNone(receipt['observed_original'])
        self.assertFalse((self.f.out/'current.original.bin').exists())

    def receipt(self, role):
        names={'serial':'frozen-serial-import.json','gui':'frozen-window.png.smoke.json',
               'recording':'frozen-recording.json'}
        return json.loads((self.f.build/names[role]).read_bytes())

    def rewrite_receipt(self, role, obj):
        names={'serial':'frozen-serial-import.json','gui':'frozen-window.png.smoke.json',
               'recording':'frozen-recording.json'}
        filename=names[role];raw=fixture_json(obj);put(self.f.build/filename,raw)
        self.f.archive['artifacts_sha256'][filename]=m.sha(raw)
        self.f.archive['artifact_bytes'][filename]=len(raw)
        if role=='gui':self.f.manifest['frozen_gui_smoke']=obj
        else:
            extension=self.f.manifest['serial_import_smoke' if role=='serial' else 'recording_smoke']
            extension['result']=obj;extension['sha256']=m.sha(raw)
        if role=='recording':
            for summary in (self.f.archive['expected_inspector_recording'],self.f.provenance['recording_smoke']):
                summary['receipt_sha256']=m.sha(raw);summary['receipt_bytes']=len(raw)
        checks=''.join(h+'  '+n+'\n' for n,h in sorted(self.f.archive['artifacts_sha256'].items()))
        put(self.f.build/'SHA256SUMS.txt',checks.encode('ascii'));self.f.save()

    def test_coherent_serial_malformed_imports_rejected(self):
        obj=self.receipt('serial');obj['imports']=None;self.rewrite_receipt('serial',obj);self.reject()

    def test_coherent_gui_boolean_dimension_rejected(self):
        obj=self.receipt('gui');obj['png']['width']=True;self.rewrite_receipt('gui',obj);self.reject()

    def test_coherent_recording_null_runtime_rejected(self):
        obj=self.receipt('recording');obj['runtime']=None;self.rewrite_receipt('recording',obj);self.reject()

    def test_coherent_recording_wrong_executable_rejected(self):
        obj=self.receipt('recording');obj['executable']['sha256']='0'*64
        self.rewrite_receipt('recording',obj);self.reject()

    def test_coherent_summary_count_rejected(self):
        for summary in (self.f.archive['expected_inspector_recording'],self.f.provenance['recording_smoke']):
            summary['segment_count']=999
        self.f.save();self.reject()

    def test_coherent_summary_device_claim_rejected(self):
        for summary in (self.f.archive['expected_inspector_recording'],self.f.provenance['recording_smoke']):
            summary['device_io_performed']=True
        self.f.save();self.reject()

    def test_package_dependency_null_rejected(self):
        self.f.manifest['packages']=None;self.f.save();self.reject()

    def test_package_platform_boolean_rejected(self):
        self.f.manifest['platform']=True;self.f.save();self.reject()

    def test_provenance_malformed_pyz_hash_rejected(self):
        self.f.provenance['pyz_sha256']='bad';self.f.save();self.reject()

    def test_provenance_boolean_payload_rejected(self):
        self.f.provenance['payload_count']=True;self.f.save();self.reject()

    def test_provenance_release_scope_rejected(self):
        self.f.provenance['scope']='release-approved';self.f.save();self.reject()

    def test_provenance_extra_native_field_rejected(self):
        self.f.provenance['native_files'][0]['invented']=42;self.f.save();self.reject()

    def test_archive_binary_count_projection_rejected(self):
        self.f.archive['binary_files']=1;self.f.save();self.reject()

    def test_nested_recording_schema_regressions(self):
        for field in ('runtime','identity','stop','segments','executable','cleanup','checks','imports','phases'):
            with self.subTest(field=field):
                obj=self.receipt('recording');obj[field]=None
                with self.assertRaises(m.Rejected):m.recording_structure(obj)

    def test_recording_bool_and_extra_schema_regressions(self):
        for parent,field,value in [('runtime','python_bits',True),('stop','request_id',True),
                                  ('cleanup','pending_io',True),('checks','invented',42)]:
            with self.subTest(parent=parent,field=field):
                obj=self.receipt('recording');obj[parent][field]=value
                with self.assertRaises(m.Rejected):m.recording_structure(obj)

    def test_retention_corruption_after_identity_write_is_not_completed(self):
        gate=self.f.gate();write=gate.io.write
        def corrupt(path,raw):
            write(path,raw)
            if path.name=='current.identity.json':put(gate.output/'current.original.bin',b'corrupt')
        with mock.patch.object(gate.io,'write',corrupt),self.assertRaises(m.Rejected):gate.retain_current()
        result=gate.failure(m.Rejected('retention-corrupt'))
        self.assertIs(result['retention_completed'],False);self.assertIsNone(result['retained_original'])
        self.assertEqual(result['observed_original'],m.record(self.f.current_path.read_bytes()))

    def test_retention_corruption_after_complete_is_not_claimed(self):
        gate=self.f.gate();gate.retain_current();put(gate.output/'current.original.bin',b'corrupt')
        result=gate.failure(m.Rejected('later-failure'))
        self.assertIs(result['retention_completed'],False);self.assertIsNone(result['retained_original'])
        self.assertTrue(result['full_original_hash_available'])

    def test_retention_verification_does_not_spend_failure_reserve(self):
        gate=self.f.gate();gate.retain_current()
        gate.io.read_bytes=m.LIMIT-m.FAILURE_RESERVE-gate.io.write_bytes
        before=gate.io.total;result=gate.failure(m.Rejected('budget'))
        self.assertIs(result['retention_completed'],False);self.assertTrue(result['full_original_hash_available'])
        self.assertLess(gate.io.total-before,m.FAILURE_RESERVE)

    def test_retention_corruption_during_failure_output_uncommitted(self):
        gate=self.f.gate();gate.retain_current();write=gate.io.write
        def corrupt(path,raw):
            write(path,raw)
            if path.name=='failure.json':put(gate.output/'current.original.bin',b'corrupt')
        with mock.patch.object(gate.io,'write',corrupt):result=gate.failure(m.Rejected('later-failure'))
        self.assertIs(result['retention_completed'],False);self.assertIsNone(result['retained_original'])
        disk=json.loads((gate.output/'failure.json').read_bytes())
        self.assertEqual(disk['failure_artifact_status'],'UNCOMMITTED_FAILURE_METADATA')

    def test_help_rejection_uses_terminal_ledger(self):
        out=subprocess.run([sys.executable,'-I','-S','-B']+(['-O'] if sys.flags.optimize else [])+
                           [str(HERE/'literal_gate.py'),'--help'],capture_output=True,timeout=10)
        self.assertEqual(out.returncode,1);self.assertEqual(out.stdout,b'')
        result=json.loads(out.stderr)
        self.assertEqual(result['terminal_bytes'],len(out.stderr))
        self.assertEqual(result['managed_bytes'],len(out.stderr))

    def test_manifest_ancestor_file_collision_rejected(self):
        self.f.manifest['files']['_internal']='0'*64
        self.f.provenance['payload_count']+=1;self.f.archive['binary_files']+=1
        self.f.save();self.reject()

    def test_manifest_case_alias_embedded_name_rejected(self):
        self.f.manifest['files']['BUILD-MANIFEST.JSON']='0'*64
        self.f.provenance['payload_count']+=1;self.f.archive['binary_files']+=1
        self.f.save();self.reject()

    def test_archive_manifest_expanded_bytes_rejected(self):
        self.f.provenance['payload_bytes']=1024*m.MIB;self.f.save();self.reject()

    def late_failure_retention(self, delete=False):
        gate=self.f.gate();gate.retain_current();read=gate.io.read;count=[0]
        def late(path,*args,**kwargs):
            result=read(path,*args,**kwargs)
            if path.name=='current.identity.json':
                count[0]+=1
                if count[0]==2:
                    target=gate.output/'current.original.bin'
                    if delete:target.unlink()
                    else:put(target,b'corrupt')
            return result
        with mock.patch.object(gate.io,'read',late):result=gate.failure(m.Rejected('later-failure'))
        self.assertIs(result['retention_completed'],False);self.assertIsNone(result['retained_original'])
        self.assertTrue(result['full_original_hash_available'])

    def test_last_identity_read_original_mutation_not_claimed(self):
        self.late_failure_retention()

    def test_last_identity_read_original_deletion_not_claimed(self):
        self.late_failure_retention(delete=True)

    def test_unrelated_failed_input_does_not_invalidate_retention(self):
        gate=self.f.gate();gate.retain_current();gate.check_joins()
        put(self.f.build/'frozen-recording.json',b'changed')
        result=gate.failure(m.Rejected('changed-other-input'))
        self.assertIs(result['retention_completed'],True)
        self.assertEqual(result['retained_original']['retained_sha256'],m.sha(self.retained()))

    def test_hardlinked_original_rejected_before_retention(self):
        os.link(self.f.current_path,self.base/'alias')
        gate=self.f.gate()
        with self.assertRaises(m.Rejected):gate.retain_current()
        result=gate.failure(m.Rejected('linked-original'))
        self.assertIs(result['retention_completed'],False)
        self.assertIs(result['full_original_hash_available'],False)

    def test_hardlinked_notice_inputs_rejected(self):
        source=self.f.root/'native-notice-source-999-1.bin'
        target=self.f.root/'native-notice-copy-999-1.bin'
        target.unlink();os.link(source,target);self.reject()

    def test_late_hardlink_of_retained_original_rejected(self):
        gate=self.f.gate();gate.retain_current()
        os.link(gate.output/'current.original.bin',self.base/'alias')
        result=gate.failure(m.Rejected('late-alias'))
        self.assertIs(result['retention_completed'],False)
        self.assertTrue(result['full_original_hash_available'])

    def test_manifest_reserved_file_as_parent_rejected(self):
        self.f.manifest['files']['BUILD-MANIFEST.JSON/child.txt']='0'*64
        self.f.provenance['payload_count']+=1;self.f.archive['binary_files']+=1
        self.f.save();self.reject()

    def test_known_notice_bytes_cannot_be_omitted_from_payload(self):
        self.f.provenance['payload_bytes']=sum(row['bytes'] for row in self.f.provenance['native_files'])
        self.f.save();self.reject()

    def test_conflicting_environment_origin_projection(self):
        rows=[row for row in self.f.provenance['native_files'] if row['origin'].get('origin_kind')=='environment']
        self.assertNotEqual(rows[0]['sha256'],rows[1]['sha256'])
        rows[1]['origin']['relative_path']=rows[0]['origin']['relative_path']
        rows[1]['origin']['distribution']=rows[0]['origin']['distribution']
        rows[1]['origin']['version']=rows[0]['origin']['version']
        self.f.save();self.reject()

    def test_conflicting_constructed_executable_origin_projection(self):
        row=next(row for row in self.f.provenance['native_files'] if row['origin'].get('origin_kind')=='environment')
        row['origin']={'status':'hash_correspondence','origin_kind':'constructed',
          'relative_path':'Motortester.exe','bytes':row['bytes'],'sha256':row['sha256']}
        self.f.save();self.reject()

if __name__=='__main__':unittest.main()
