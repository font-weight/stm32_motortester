"""Opaque synthetic prelaunch tests. No native execution or build proof."""
import importlib.util
import json
import marshal
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

HERE=Path(__file__).resolve().parent
BASE=Path(os.environ.get('PRELAUNCH_INPUT_ROOT',str(HERE.parents[3])))

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    return obj

p=load('prelaunch_tested',HERE/'prelaunch.py')
INPUT=BASE/'prelaunch-inputs-20261015'
COMP=INPUT/'companion/host/packaging/pyz-replacement-probe/pyz_replacement_probe.py'
INSP=INPUT/'inspector/host/packaging/inspect_candidate_provenance.py'
c=load('companion_fixture',COMP);t=load('inspector_fixture',INSP)
ORIGINAL=(INPUT/'official-original/original-init.py').read_bytes()
SOURCE=BASE/'app244b-sourcezip-20261015/app244b-host-source-exact.zip'
TRIAL='0123456789abcdef0123456789abcdef';EPOCH=1700000000

def put(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)

def oracle(raw,offset):
    # Independent DWORD arithmetic from pefile v2024.8.26, not comparator calls.
    n=len(raw);data=raw+b'\0'*((-n)%4);total=0
    for at in range(0,len(data),4):
        if at==offset:continue
        total+=int.from_bytes(data[at:at+4],'little')
        if total>=2**32:total=(total&0xffffffff)+(total>>32)
    total=(total&0xffff)+(total>>16)
    total=(total&0xffff)+(total>>16)
    return total+n

def prefix():
    data=bytearray(1024);data[:2]=b'MZ';struct.pack_into('<I',data,60,128)
    data[128:132]=b'PE\0\0';struct.pack_into('<HHI',data,132,0x8664,1,EPOCH)
    struct.pack_into('<H',data,148,240);opt=152
    struct.pack_into('<H',data,opt,0x20b);struct.pack_into('<I',data,opt+60,512)
    struct.pack_into('<I',data,opt+108,16)
    struct.pack_into('<II',data,opt+32,4096,512)
    struct.pack_into('<II',data,opt+240+8,512,4096)
    struct.pack_into('<II',data,opt+240+16,512,512)
    return data

def checked(raw):
    data=bytearray(raw);struct.pack_into('<I',data,216,oracle(data,216));return bytes(data)

def pyz(target=b'original opaque target',extra=b'unchanged opaque content',gap=b'',tail=b''):
    raw=bytearray(b'PYZ\0'+p.MAGIC+b'\0'*9);rows=[]
    for name,kind,body in [('PySide6',1,target),('unchanged',0,extra)]:
        raw.extend(gap);stored=zlib.compress(body)
        rows.append((name,(kind,len(raw),len(stored))));raw.extend(stored)
    raw.extend(tail);off=len(raw);raw.extend(marshal.dumps(rows,4));raw[8:12]=struct.pack('!i',off)
    return bytes(raw)

def exe(payload,bootstrap=b'opaque bootstrap',gap=b'',tail=b''):
    body=bytearray();toc=bytearray()
    for name,kind,raw in [('bootstrap',b'm',bootstrap),('pyi-contents-directory _internal',b'o',b''),('PYZ.pyz',b'z',payload)]:
        body.extend(gap if kind!=b'o' else b'');label=name.encode()+b'\0'
        size=(t.ENTRY.size+len(label)+15)//16*16
        toc.extend(t.ENTRY.pack(size,len(body),len(raw),len(raw),0,kind));toc.extend(label.ljust(size-t.ENTRY.size,b'\0'))
        body.extend(raw)
    body.extend(tail)
    cookie=t.COOKIE.pack(t.COOKIE_MAGIC,len(body)+len(toc)+t.COOKIE.size,len(body),len(toc),313,b'python313.dll'.ljust(64,b'\0'))
    return checked(prefix()+body+toc+cookie)

class Fixture:
    def __init__(self,parent):
        self.root=parent/'trial';self.record_path=parent/'declarations/build-record.json'
        self.output=parent/'output/report.json';self.output.parent.mkdir()
        modified,patch=c.modification(ORIGINAL,TRIAL)
        self.prep=c.preparation_manifest(TRIAL,ORIGINAL,modified,patch,c.sha(COMP.read_bytes()))
        for name,body in [('original-init.py',ORIGINAL),('modified-init.py',modified),('change.patch',patch),('preparation.json',c.encoded(self.prep))]:
            put(self.root/'preparation'/name,body)
        for i,v in enumerate(c.VARIANTS):
            base=self.root/v;payload=pyz(b'original opaque target' if i==0 else b'modified target is longer opaque fixture')
            put(base/'bundle/Motortester.exe',exe(payload));put(base/'PYZ-00.pyz',payload)
            put(base/'bundle/_internal/example.dll',b'opaque DLL data, never loaded')
            put(base/'bundle/_internal/config.dat',b'unchanged nonnative runtime')
            (base/'bundle/replacement-probe').mkdir();(base/'receipts').mkdir()
            put(base/'build.log',b'synthetic declaration only; no build executed\n')
            put(base/'source-inputs.json',c.encoded({'PySide6':p.byte_record(ORIGINAL if i==0 else modified),
                'unchanged':p.byte_record(b'opaque source')}))
        self.config={'self':HERE/'prelaunch.py','inspector':INSP,'companion':COMP,'pins':HERE/'pins.json',
                     'source_zip':SOURCE,'build_record':self.record_path,'trial':self.root,'output':self.output}
        self.refresh()
    def refresh(self):
        pins=json.loads((HERE/'pins.json').read_bytes());builds={}
        for v in c.VARIANTS:
            base=self.root/v;bundle=base/'bundle'
            inv={f.relative_to(bundle).as_posix():p.byte_record(f.read_bytes()) for f in bundle.rglob('*') if f.is_file()}
            builds[v]={'initializer':self.prep['files']['original-init.py' if v=='control' else 'modified-init.py'],
                'source_inputs':p.byte_record((base/'source-inputs.json').read_bytes()),
                'build_log':p.byte_record((base/'build.log').read_bytes()),
                'executable':p.byte_record((bundle/'Motortester.exe').read_bytes()),
                'work_pyz':p.byte_record((base/'PYZ-00.pyz').read_bytes()),
                'bundle_inventory_sha256':p.sha(c.encoded(inv)),'source_tree_unchanged':True,
                'changed_inputs':[] if v=='control' else [c.MEMBER],'build_succeeded':True}
        self.record={'schema_version':1,'scope':'wrapper-declared-prelaunch-inputs','trial_id':TRIAL,
            'application':{'commit':c.APP,'tree':c.TREE},'source_zip':{'bytes':c.SOURCE_BYTES,'sha256':c.SOURCE_SHA},
            'tools':{k:p.byte_record(self.config[k].read_bytes()) for k in ('inspector','companion')},
            'app_inputs':pins['app_inputs'],'wheels':pins['wheels'],
            'runtime':{'platform':'win32','python':'3.13.16','machine':'AMD64','free_threaded':False,'python_magic':p.MAGIC.hex()},
            'source_date_epoch':EPOCH,'prefix_policy':p.PREFIX_POLICY,'builds':builds,
            'verified_by_comparator':{'wheel_bodies':False,'runtime':False,'build_execution':False,'source_to_bytecode':False}}
        self.record['tools']['comparator']=p.byte_record((HERE/'prelaunch.py').read_bytes());self.save()
    def save(self):put(self.record_path,c.encoded(self.record))
    def run(self):return p.compare(self.config)
    def replace_exe(self,body):put(self.root/'modified/bundle/Motortester.exe',body);self.refresh()

class Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='prelaunch-opaque-');self.addCleanup(temp.cleanup)
        self.f=Fixture(Path(temp.name))
    def reject(self,output_written=False):
        try:
            self.f.run()
        except Exception as error:
            self.assertIn((type(error).__module__,type(error).__name__),
                [('prelaunch_tested','Rejected'),('_pinned_companion','Rejected'),
                 ('_pinned_inspector','Invalid')])
        else:self.fail('invalid fixture accepted')
        self.assertEqual(self.f.output.exists(),output_written)
    def test_positive(self):
        result=self.f.run();self.assertEqual(result['status'],'PRELAUNCH_BYTES_MATCH')
        report=json.loads(self.f.output.read_bytes())
        self.assertEqual(report['status'],'UNCOMMITTED_PRELAUNCH_COMPARISON')
        for field in p.FALSE_FIELDS:self.assertIs(result[field],False);self.assertIs(report[field],False)
        self.assertLessEqual(result['managed_bytes'],p.MAX_IO)
    def test_checksum_oracle(self):
        for length in (4,5,6,7,8,511,512,513,1023,65537):
            for fill in (0,17,34,255):
                raw=bytes([fill])*length
                self.assertEqual(p.pe_checksum(raw,0),oracle(raw,0))
    def test_runtime_change(self):
        put(self.f.root/'modified/bundle/_internal/config.dat',b'changed');self.f.refresh();self.reject()
    def test_native_change(self):
        put(self.f.root/'modified/bundle/_internal/example.dll',b'changed');self.f.refresh();self.reject()
    def test_nontarget_change(self):self.f.replace_exe(exe(pyz(b'new target',b'changed other')));self.reject()
    def test_target_unchanged(self):self.f.replace_exe((self.f.root/'control/bundle/Motortester.exe').read_bytes());self.reject()
    def test_bootstrap_change(self):self.f.replace_exe(exe(pyz(b'new target'),b'changed bootstrap'));self.reject()
    def test_pyz_gap_change(self):self.f.replace_exe(exe(pyz(b'new target',gap=b'X')));self.reject()
    def test_archive_gap_change(self):self.f.replace_exe(exe(pyz(b'new target'),gap=b'X'));self.reject()
    def test_archive_tail_change(self):self.f.replace_exe(exe(pyz(b'new target'),tail=b'X'));self.reject()
    def test_prefix_change(self):
        raw=bytearray((self.f.root/'modified/bundle/Motortester.exe').read_bytes());raw[900]^=1
        self.f.replace_exe(checked(raw));self.reject()
    def test_bad_checksum(self):
        raw=bytearray((self.f.root/'modified/bundle/Motortester.exe').read_bytes());raw[216]^=1
        self.f.replace_exe(bytes(raw));self.reject()
    def test_wrong_epoch(self):self.f.record['source_date_epoch']+=1;self.f.save();self.reject()
    def test_bool_epoch(self):self.f.record['source_date_epoch']=True;self.f.save();self.reject()
    def test_unknown_record_key(self):self.f.record['unexpected']='anything';self.f.save();self.reject()
    def test_claimed_runtime_verified(self):self.f.record['verified_by_comparator']['runtime']=True;self.f.save();self.reject()
    def test_preexisting_marker(self):put(self.f.root/'modified/bundle/replacement-probe/marker.json',b'{}');self.reject()
    def test_preexisting_receipt(self):put(self.f.root/'control/receipts/result.json',b'{}');self.reject()
    def test_execution_record(self):put(self.f.root/'execution.json',b'{}');self.reject()
    def test_wrong_source(self):
        path=self.f.root/'modified/source-inputs.json';data=json.loads(path.read_bytes());data['unchanged']['bytes']+=1
        put(path,c.encoded(data));self.f.refresh();self.reject()
    def test_case_alias(self):
        (self.f.root/'modified/bundle/_INTERNAL').mkdir();self.reject()
    def test_symlink(self):
        (self.f.root/'modified/bundle/_internal/link').symlink_to('example.dll');self.reject()
    def test_budget(self):
        with mock.patch.object(p,'MAX_IO',1024):self.reject()
    def test_source_zip_tamper(self):
        path=self.f.record_path.parent/'source.zip';put(path,b'bad ZIP');self.f.config['source_zip']=path;self.reject()
    def test_work_difference_is_qualified(self):
        put(self.f.root/'modified/PYZ-00.pyz',pyz(b'work only',b'work changed other'));self.f.refresh()
        self.f.run();report=json.loads(self.f.output.read_bytes())
        self.assertFalse(report['variants']['modified']['work_matches_embedded'])
        self.assertEqual(report['work_comparison']['counts']['changed'],2)
    def test_no_process_or_code_deserialization(self):
        with mock.patch('subprocess.Popen',side_effect=AssertionError('process launch')) as launch, \
             mock.patch('marshal.loads',side_effect=AssertionError('marshal load')) as unmarshal:
            self.f.run();launch.assert_not_called();unmarshal.assert_not_called()
    def test_final_rehash_catches_same_size_edit(self):
        original=p.Inputs.recheck
        def changed(inputs):
            path=self.f.root/'control/bundle/_internal/config.dat'
            old=path.stat();raw=bytearray(path.read_bytes());raw[0]^=1;path.write_bytes(raw)
            os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
            return original(inputs)
        with mock.patch.object(p.Inputs,'recheck',changed):self.reject()
    def test_ordinary_ancestor_swap(self):
        victim=self.f.root/'control/bundle/_internal'
        replacement=self.f.root.parent/'replacement';replacement.mkdir()
        for leaf in victim.iterdir():os.link(leaf,replacement/leaf.name)
        original=p.Inputs.recheck
        def swapped(inputs):
            victim.rename(self.f.root.parent/'previous-directory');replacement.rename(victim)
            return original(inputs)
        with mock.patch.object(p.Inputs,'recheck',swapped):self.reject()
    def test_empty_directory_swap(self):
        victim=self.f.root/'modified/bundle/replacement-probe'
        original=p.Inputs.recheck
        def swapped(inputs):
            victim.rename(self.f.root.parent/'previous-marker-directory');victim.mkdir()
            return original(inputs)
        with mock.patch.object(p.Inputs,'recheck',swapped):self.reject()
    def test_output_parent_swap(self):
        victim=self.f.output.parent;original=p.Inputs.recheck
        def swapped(inputs):
            victim.rename(self.f.root.parent/'previous-output-directory');victim.mkdir()
            return original(inputs)
        with mock.patch.object(p.Inputs,'recheck',swapped):self.reject()
    def test_same_gap_bytes_allowed(self):
        for v in c.VARIANTS:
            target=b'original' if v=='control' else b'modified longer target'
            put(self.f.root/v/'bundle/Motortester.exe',exe(pyz(target,gap=b'AB',tail=b'CD'),gap=b'EF',tail=b'GH'))
        self.f.refresh();self.f.run()
    def test_pe_header_mutations(self):
        original=(self.f.root/'modified/bundle/Motortester.exe').read_bytes()
        changes=[(60,'<I',129),(132,'<H',0x14c),(134,'<H',0),(148,'<H',224),
                 (152,'<H',0x10b),(212,'<I',100),(260,'<I',15),
                 (296,'<I',1),(408,'<I',999999),(412,'<I',1)]
        for offset,fmt,value in changes:
            with self.subTest(offset=offset):
                raw=bytearray(original);struct.pack_into(fmt,raw,offset,value)
                self.f.replace_exe(checked(raw));self.reject()
    def debug_pair(self):
        for v in c.VARIANTS:
            path=self.f.root/v/'bundle/Motortester.exe';raw=bytearray(path.read_bytes())
            struct.pack_into('<II',raw,312,4096,28)
            struct.pack_into('<I',raw,516,EPOCH)
            struct.pack_into('<III',raw,528,16,4160,576)
            raw[576:592]=b'opaque-debugdata'
            put(path,checked(raw))
        self.f.refresh()
    def test_debug_valid(self):self.debug_pair();self.f.run()
    def test_debug_mapping_rejections(self):
        self.debug_pair();original=(self.f.root/'modified/bundle/Motortester.exe').read_bytes()
        for offset,value in [(312,999999),(316,27),(316,28*129),(516,EPOCH+1),
                             (528,999999),(532,999999),(536,999999),(536,600)]:
            with self.subTest(offset=offset,value=value):
                # Both variants receive the same malformed DEBUG bytes: equality alone must not suffice.
                for v in c.VARIANTS:
                    path=self.f.root/v/'bundle/Motortester.exe';raw=bytearray(path.read_bytes())
                    # Reset only prefix to valid original, retaining each variant's PKG.
                    raw[:1024]=original[:1024];struct.pack_into('<I',raw,offset,value)
                    put(path,checked(raw))
                self.f.refresh();self.reject()
    def test_identical_prefix_trailer_rejected(self):
        for v in c.VARIANTS:
            path=self.f.root/v/'bundle/Motortester.exe';raw=path.read_bytes()
            put(path,checked(raw[:1024]+b'extra-overlay'+raw[1024:]))
        self.f.refresh();self.reject()
    def test_json_duplicate_keys(self):
        raw=self.f.record_path.read_bytes();put(self.f.record_path,raw.replace(b'{',b'{"schema_version":1,',1));self.reject()
    def test_stale_binding(self):
        self.f.record['builds']['modified']['executable']['sha256']='a'*64;self.f.save();self.reject()
    def test_wrong_pins_body(self):
        path=self.f.record_path.parent/'pins.json';put(path,b'{}');self.f.config['pins']=path;self.reject()
    def test_wrong_tool_body(self):
        path=self.f.record_path.parent/'inspector.py';put(path,b'raise RuntimeError("must not execute")')
        self.f.config['inspector']=path;self.reject()
    def test_target_missing(self):
        payload=pyz(b'new target');off=struct.unpack('!i',payload[8:12])[0]
        # Restricted pinned data decoder, never marshal.loads.
        rows=t.TocData(payload[off:]).parse();rows[0]=('Different',rows[0][1])
        self.f.replace_exe(exe(payload[:off]+marshal.dumps(rows,4)));self.reject()
    def test_target_not_package(self):
        payload=pyz(b'new target');off=struct.unpack('!i',payload[8:12])[0]
        rows=t.TocData(payload[off:]).parse();name,(_,start,size)=rows[0];rows[0]=(name,(0,start,size))
        self.f.replace_exe(exe(payload[:off]+marshal.dumps(rows,4)));self.reject()
    def test_output_exists(self):
        put(self.f.output,b'keep')
        with self.assertRaises(p.Rejected):self.f.run()
        self.assertEqual(self.f.output.read_bytes(),b'keep')

    def terminal_readback_mutation(self,kind):
        original=p.Inputs.read;reads=[0]
        def injected(inputs,path,*args,**kwargs):
            raw=original(inputs,path,*args,**kwargs)
            if path==self.f.output:
                reads[0]+=1
                if reads[0]==2:
                    bundle=self.f.root/'modified/bundle'
                    if kind=='source':put(bundle/'_internal/config.dat',b'changed runtime')
                    elif kind=='marker':put(bundle/'replacement-probe/late.json',b'{}')
                    elif kind=='leaf':put(bundle/'late.dat',b'late runtime')
                    elif kind=='alias':put(self.f.output.with_name('REPORT.JSON'),b'alias')
            return raw
        with mock.patch.object(p.Inputs,'read',injected):self.reject(output_written=True)
        self.assertEqual(reads[0],2)
        self.assertTrue(self.f.output.exists())  # Late rejection preserves the unaccepted artifact.

    def test_terminal_runtime_change(self):self.terminal_readback_mutation('source')
    def test_terminal_marker(self):self.terminal_readback_mutation('marker')
    def test_terminal_bundle_leaf(self):self.terminal_readback_mutation('leaf')
    def test_terminal_output_alias(self):self.terminal_readback_mutation('alias')

    def cross_api_stamp(self,field):
        import types
        original=os.fstat
        def adjusted(fd):
            value=original(fd)
            fields={key:getattr(value,key) for key in dir(value) if key.startswith('st_')}
            fields[field]+=500
            return types.SimpleNamespace(**fields)
        return mock.patch.object(os,'fstat',adjusted)

    def test_cross_api_mtime_rejected(self):
        with self.cross_api_stamp('st_mtime_ns'):self.reject()

    def test_cross_api_ctime_qualified(self):
        with self.cross_api_stamp('st_ctime_ns'):
            self.assertEqual(self.f.run()['status'],'PRELAUNCH_BYTES_MATCH')

if __name__=='__main__':unittest.main()
