"""Bounded source-byte observations; no installation, build or launch authority."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import types
import zipfile

LIMIT=256*1024*1024
META=2*1024*1024
PRELAUNCH_SHA='c885445f3f711894ef6ec431248b14b51b797e3cc0635182fb7508cb134120b2'
PRELAUNCH_BYTES=31571
FALSE_FIELDS=('source_pool_admission_verified','runtime_verified','wheel_bodies_verified',
              'build_execution_verified','source_to_bytecode_attestation','launch_authorized')

class Rejected(Exception):pass

def need(condition,code):
    if not condition:raise Rejected(code)

def digest(raw):return hashlib.sha256(raw).hexdigest()

def bootstrap(path):
    """Read the one exact trusted helper before its path/I/O API is available."""
    path=Path(path)
    need(path.is_absolute() and str(path)==os.path.normpath(str(path)),'bootstrap-path')
    dirs={}
    for ancestor in reversed(path.parents):
        s=ancestor.lstat()
        need(stat.S_ISDIR(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400,
             'bootstrap-ancestor')
        dirs[ancestor]=(s.st_dev,s.st_ino)
    before=path.lstat()
    need(stat.S_ISREG(before.st_mode) and not getattr(before,'st_file_attributes',0)&0x400
         and before.st_size==PRELAUNCH_BYTES,'bootstrap-file')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
    try:
        opened=os.fstat(fd)
        need(stat.S_ISREG(opened.st_mode) and not getattr(opened,'st_file_attributes',0)&0x400,
             'bootstrap-open-type')
        need((opened.st_dev,opened.st_ino,opened.st_size)==(before.st_dev,before.st_ino,before.st_size),
             'bootstrap-open')
        raw=os.read(fd,PRELAUNCH_BYTES+1)
        end=os.fstat(fd)
        need(stat.S_ISREG(end.st_mode) and not getattr(end,'st_file_attributes',0)&0x400,
             'bootstrap-end-type')
        need((opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns)==
             (end.st_dev,end.st_ino,end.st_size,end.st_mtime_ns,end.st_ctime_ns),'bootstrap-handle')
    finally:os.close(fd)
    after=path.lstat()
    need(stat.S_ISREG(after.st_mode) and not getattr(after,'st_file_attributes',0)&0x400,
         'bootstrap-final-type')
    need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==
         (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'bootstrap-changed')
    for ancestor,identity in dirs.items():
        s=ancestor.lstat();need(stat.S_ISDIR(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400
                              and (s.st_dev,s.st_ino)==identity,'bootstrap-ancestor-changed')
    need(len(raw)==PRELAUNCH_BYTES and digest(raw)==PRELAUNCH_SHA,'bootstrap-hash')
    module=types.ModuleType('_source_prelaunch');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module,raw,dirs


def collect(config):
    p,bootstrap_raw,bootstrap_dirs=bootstrap(config['prelaunch'])
    class Reads(p.Inputs):
        def read(self,path,cap=65536,keep=True):
            remaining=LIMIT-self.total
            need(remaining>0,'source-budget')
            result=super().read(path,min(cap,remaining),keep)
            need(self.total<=LIMIT,'source-budget')
            return result
        def write(self,path,raw):
            need(0<len(raw)<=META and 3*len(raw)<=LIMIT-self.total,'source-output-budget')
            self.pin_directory(path.parent)
            output_name(path,False)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|
                       getattr(os,'O_NOFOLLOW',0),0o600)
            try:
                first=os.fstat(fd);need(p.regular(first) and first.st_size==0,'source-output-handle')
                pos=0
                while pos<len(raw):
                    n=os.write(fd,raw[pos:pos+65536]);need(0<n<=min(65536,len(raw)-pos),'source-write')
                    self.write_bytes+=n;pos+=n;need(self.total<=LIMIT,'source-budget')
                last=os.fstat(fd)
                need(p.regular(last) and p.shared(first)[:2]==p.shared(last)[:2] and
                     last.st_size==len(raw),'source-output-handle')
            finally:os.close(fd)
            need(p.shared(p.info(path))==p.shared(last),'source-output-path')
            self.unchanged_directories()
            need(self.read(path,META)==raw,'source-output-readback')
    paths={key:p.path_value(config[key]) for key in ('self','prelaunch','inspector','companion','pins',
          'source_zip','preparation','pool_manifest','app','site','stdlib','exe','work_pyz','pure_toc',
          'source_output','sidecar_output')}
    need(config['variant'] in ('control','modified'),'variant')
    need(type(config['pool_sha256']) is str and len(config['pool_sha256'])==64 and
         all(c in '0123456789abcdef' for c in config['pool_sha256']),'pool-hash-format')
    roots={k:paths[k] for k in ('app','site','stdlib')}
    variant_root=paths['exe'].parent.parent;trial_root=variant_root.parent
    need(paths['exe'].name=='Motortester.exe' and paths['exe'].parent.name=='bundle' and
         variant_root.name==config['variant'] and paths['preparation']==trial_root/'preparation',
         'source-trial-layout')
    need(not paths['sidecar_output'].is_relative_to(trial_root),'sidecar-trial-overlap')
    need(not paths['source_output'].is_relative_to(trial_root) or
         paths['source_output']==variant_root/'source-inputs.json','source-output-trial-location')
    need(len({str(v).casefold() for v in paths.values()})==len(paths),'argument-alias')
    for a,root in roots.items():
        need(all(a==b or (root not in other.parents and other not in root.parents)
                 for b,other in roots.items()),'source-root-overlap')
    outputs=[paths['source_output'],paths['sidecar_output']]
    for output in outputs:
        need(all(root not in output.parents and output not in root.parents for root in roots.values()),
             'output-source-overlap')
        need(paths['preparation'] not in output.parents,'output-preparation-overlap')
        need(p.info(output,missing=True) is None,'source-output-exists')
    reads=Reads();reads.read_bytes=len(bootstrap_raw);reads.directories.update(bootstrap_dirs)
    for root in [*roots.values(),paths['preparation'],*[x.parent for x in outputs]]:reads.pin_directory(root)
    need(reads.read(paths['prelaunch'],128*1024)==bootstrap_raw,'bootstrap-reread')
    own=reads.read(paths['self'],128*1024)
    tool,tool_id=p.load_verified(reads,paths['inspector'],53390,p.INSPECTOR_HASH,'_source_inspector')
    comp,comp_id=p.load_verified(reads,paths['companion'],34277,p.COMPANION_HASH,'_source_companion')
    reads.tool=tool
    def output_name(path,present):
        reads.pin_directory(path.parent)
        if not present:comp.require_new_name(path.parent,path.name);return
        found=False;count=0
        with os.scandir(path.parent) as entries:
            for entry in entries:
                count+=1;need(count<=4096,'output-parent-limit')
                if entry.name.casefold()==path.name.casefold():
                    need(entry.name==path.name and not found,'output-case-alias');found=True
        need(found,'output-name-missing')
    for output in outputs:output_name(output,False)
    pins_raw=reads.read(paths['pins']);need(digest(pins_raw)==p.PINS_HASH,'pins-body')
    pins=tool.manifest_json(pins_raw)
    source_zip=reads.read(paths['source_zip'],comp.SOURCE_BYTES)
    need(len(source_zip)==comp.SOURCE_BYTES and digest(source_zip)==comp.SOURCE_SHA,'source-zip')
    app_expected={}
    with zipfile.ZipFile(io.BytesIO(source_zip)) as archive:
        # Exact ZIP hash was checked before interpreting members.
        for member in archive.infolist():
            if not member.is_dir() and member.filename.startswith('host/') and member.filename.endswith('.py'):
                need(0<=member.file_size<=META,'app-source-size')
                raw=archive.read(member)
                app_expected[member.filename[5:]]={'bytes':len(raw),'sha256':digest(raw),'owner':comp.APP}
    prep=comp.read_preparation(reads,paths['preparation'])
    pool_raw=reads.read(paths['pool_manifest'],META)
    need(digest(pool_raw)==config['pool_sha256'],'pool-hash')
    pool=tool.manifest_json(pool_raw)
    need(type(pool) is dict and set(pool)=={'schema_version','scope','trial_id','application','roots','files'},'pool-schema')
    expected_header={'schema_version':1,'scope':'wrapper-frozen-source-pools','trial_id':prep['trial_id'],
                     'application':{'commit':comp.APP,'tree':comp.TREE},
                     'roots':{k:str(v) for k,v in roots.items()}}
    need(tool.same_json({k:pool[k] for k in expected_header},expected_header),'pool-header')
    need(type(pool['files']) is dict and set(pool['files'])==set(roots),'pool-files')
    owners={r['name'] for r in pins['wheels']};count=0
    for category,entries in pool['files'].items():
        need(type(entries) is dict,'pool-category')
        folded=set()
        for rel,record in entries.items():
            count+=1;need(count<=16384,'pool-entry-limit')
            need(type(rel) is str and tool.relative(rel)==rel and '\\' not in rel and
                 rel.endswith('.py') and rel.casefold() not in folded,'pool-relative')
            folded.add(rel.casefold())
            need(type(record) is dict and set(record)=={'bytes','sha256','owner'},'pool-leaf-schema')
            comp.integer(record['bytes'],0,META);comp.hexadecimal(record['sha256'])
            expected_owner=comp.APP if category=='app' else 'trusted-runtime-observed'
            need(record['owner'] in owners if category=='site' else record['owner']==expected_owner,'pool-owner')
            if category=='stdlib':need('site-packages' not in [x.casefold() for x in Path(rel).parts],'stdlib-site')
    need(tool.same_json(pool['files']['app'],app_expected),'app-pool-map')
    original=prep['files']['original-init.py'];target_rel='PySide6/__init__.py'
    target_record=pool['files']['site'].get(target_rel)
    need(type(target_record) is dict and target_record['owner']=='PySide6-Essentials' and
         {k:target_record[k] for k in ('bytes','sha256')}==original,'original-target-pool')
    exe=reads.read(paths['exe'],64*p.MIB);work=reads.read(paths['work_pyz'],64*p.MIB)
    inv=tool.archive_inventory(exe,p.MAGIC);work_modules=tool.pyz_inventory(work,p.MAGIC)
    toc_raw=reads.read(paths['pure_toc'],META);guts=tool.literals(toc_raw)
    need(type(guts) is tuple and len(guts)==2 and type(guts[0]) is str and
         str(p.path_value(guts[0]))==str(paths['work_pyz']),'toc-work-path')
    pure=tool.pure_map(toc_raw,paths['work_pyz'])
    need(len({name.casefold() for name in pure})==len(pure),'pure-case-alias')
    names=[r['name'] for r in inv[3]]
    need(len({name.casefold() for name in names})==len(names) and set(names)<=set(pure),'embedded-source-set')
    need(sum(name.casefold()=='pyside6' for name in names)==1 and 'PySide6' in names,'source-target')
    need(next(row for row in inv[3] if row['name']=='PySide6')['kind']=='package','target-package')
    plan={};associations={};table={}
    for row in inv[3]:
        name=row['name'];source=pure[name]
        need((row['kind']=='namespace')==(source is None or source=='-'),'source-namespace')
        if row['kind']=='namespace':table[name]=None;associations[name]={'category':'namespace'};continue
        need(type(source) is str,'source-path-type');path=p.path_value(source)
        category=None;rel=None
        for label,root in roots.items():
            if path.is_relative_to(root):category=label;rel=path.relative_to(root).as_posix();break
        need(category is not None and str(path)==str(roots[category]/rel),'source-root')
        entries=pool['files'][category]
        need(rel in entries and path.suffix=='.py','source-membership')
        need((row['kind']=='package')==(path.name=='__init__.py'),'source-package')
        expected={k:entries[rel][k] for k in ('bytes','sha256')}
        if name=='PySide6':
            need(category=='site' and rel==target_rel and row['kind']=='package','target-source-path')
            expected=prep['files']['original-init.py' if config['variant']=='control' else 'modified-init.py']
        elif category=='site':need(rel!=target_rel,'target-source-alias')
        key=str(path)
        need(key not in plan or plan[key]['expected']==expected,'source-shared-conflict')
        plan[key]={'path':path,'expected':expected,'category':category}
        need(len(plan)<=8192,'source-plan-limit')
        associations[name]={'category':category,'relative_path':rel,'owner':entries[rel]['owner'],**expected}
        table[name]=expected
    # Complete lexical/membership plan precedes every source-leaf open.
    identities={record[0][:2]:path for path,record in reads.seen.items()};stdlib_ancestors=set()
    for item in plan.values():
        path=item['path'];reads.pin_directory(path.parent);s=p.info(path)
        need(s.st_size==item['expected']['bytes'],'source-size')
        # Bind the completed plan to the subsequent first read, not a new snapshot.
        need(path not in reads.seen,'source-metadata-overlap')
        reads.seen[path]=(p.shared(s),p.stamp(s),item['expected']['sha256'])
        ident=(s.st_dev,s.st_ino)
        need(ident not in identities or identities[ident]==path,'source-hardlink-alias');identities[ident]=path
        if item['category']=='stdlib':
            for ancestor in (path.parent,*path.parent.parents):
                if not ancestor.is_relative_to(roots['stdlib']):break
                stdlib_ancestors.add(ancestor)
                try:(ancestor/'pyvenv.cfg').lstat()
                except FileNotFoundError:continue
                raise Rejected('stdlib-nested-environment')
    # Existing metadata is reread once; each planned source is read and reread.
    need(sum(x['expected']['bytes'] for x in plan.values())+
         sum(v[0][2] for v in reads.seen.values())+12*p.MIB<=LIMIT-reads.total,'source-plan-budget')
    for item in plan.values():
        size,hashed=reads.read(item['path'],META,keep=False)
        need({'bytes':size,'sha256':hashed}==item['expected'],'source-body')
    def check_stdlib_absence():
        for ancestor in stdlib_ancestors:
            reads.pin_directory(ancestor)
            try:(ancestor/'pyvenv.cfg').lstat()
            except FileNotFoundError:continue
            raise Rejected('stdlib-nested-environment')
    reads.recheck();check_stdlib_absence();binding=reads.input_digest()
    result={'schema_version':1,'status':'UNCOMMITTED_SOURCE_OBSERVATION','trial_id':prep['trial_id'],
        'variant':config['variant'],'scope':'source-byte-association-only',
        'pool_manifest':p.byte_record(pool_raw),'application':pool['application'],
        'tools':{'collector':p.byte_record(own),'prelaunch':p.byte_record(bootstrap_raw),
                 'inspector':tool_id,'companion':comp_id},'source_zip':p.byte_record(source_zip),
        'exe':p.byte_record(exe),'embedded_pyz':p.byte_record(inv[2]),'work_pyz':p.byte_record(work),
        'work_matches_embedded':work==inv[2],'work_module_count':len(work_modules),
        'pure_toc':p.byte_record(toc_raw),'declared_only_names':sorted(set(pure)-set(names)),
        'associations':associations,'inspected_input_set_sha256':binding,
        'wrapper_admission_evidence_required':True,**{k:False for k in FALSE_FIELDS}}
    table_raw=comp.encoded(table);sidecar_raw=comp.encoded(result)
    need(len(table_raw)<=META and len(sidecar_raw)<=META,'source-output-size')
    need(3*(len(table_raw)+len(sidecar_raw))<=LIMIT-reads.total,'source-output-budget')
    reads.write(paths['source_output'],table_raw);reads.write(paths['sidecar_output'],sidecar_raw)
    reads.unchanged()
    need(reads.read(paths['source_output'],META)==table_raw and
         reads.read(paths['sidecar_output'],META)==sidecar_raw,'source-final-readback')
    for output in outputs:output_name(output,True)
    reads.unchanged();check_stdlib_absence()
    return {'schema_version':1,'status':'SOURCE_BYTES_OBSERVED',
        'source_table':p.byte_record(table_raw),'sidecar':p.byte_record(sidecar_raw),
        'inspected_input_set_sha256':binding,'read_bytes':reads.read_bytes,'write_bytes':reads.write_bytes,
        'managed_bytes':reads.total,'managed_limit':LIMIT,**{k:False for k in FALSE_FIELDS}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('prelaunch','inspector','companion','pins','source-zip','preparation','pool-manifest','pool-sha256',
                'app','site','stdlib','exe','work-pyz','pure-toc','variant','source-output','sidecar-output'):
        parser.add_argument('--'+arg,required=True)
    args=vars(parser.parse_args())
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'isolated-python-required')
    need(sys.platform=='win32' and sys.version_info[:3]==(3,13,16),'windows-runtime-required')
    args['self']=str(Path(__file__).absolute())
    print(json.dumps(collect(args),sort_keys=True,allow_nan=False));return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Rejected as error:
        print('SOURCE_REJECT '+str(error),file=sys.stderr);raise SystemExit(1)
    except Exception:
        print('SOURCE_REJECT external-input-or-io',file=sys.stderr);raise SystemExit(1)
