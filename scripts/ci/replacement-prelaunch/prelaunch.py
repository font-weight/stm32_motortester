"""Opaque prelaunch byte comparator; never authorize or execute candidate code."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import types

MIB = 1024 * 1024
MAX_IO = 1024 * MIB
MAX_INPUTS = 8300
MAX_FILE = 64 * MIB
MAX_REPORT = 65536
INSPECTOR_HASH = 'fa017e8ab7a348d45c74c86bb877968bac9aef77f963642cc7af396a5f2c8c53'
COMPANION_HASH = '0ac8579b8a8a78918db11c981629b426fe3d20c96f34b57a3226a7021adf3efe'
PINS_HASH = 'd5eb3c4310b91dc509ccb9beef64dc94244453b37c072467aa6ea71459acc27b'
MAGIC = b'\xf3\r\r\n'
FALSE_FIELDS = ('actual_windows_execution_verified','pyz_recombination_observed',
                'native_replacement_verified','native_embedded_support_verified',
                'corresponding_source_verified','source_to_bytecode_attestation',
                'legal_clearance','distribution_clearance','wheel_bodies_verified',
                'runtime_verified','build_execution_verified','launch_authorized')

class Rejected(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def require(value, code):
    if not value:
        raise Rejected(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def encoded(value):
    return (json.dumps(value,ensure_ascii=True,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('ascii')

def path_value(value):
    raw=os.fspath(value)
    require(type(raw) is str and 0<len(raw.encode('utf-8'))<=1024 and
            not any(ord(c)<32 or ord(c)==127 for c in raw),'path-text')
    p=Path(raw)
    require(p.is_absolute() and os.path.normpath(raw)==raw and str(p)==raw and
            len(p.parts)<=64,'path-canonical')
    if os.name=='nt':
        require(re.fullmatch('[A-Za-z]:',p.drive) is not None,'path-drive')
    else:
        require(p.anchor=='/' and '\\' not in raw,'path-anchor')
    for part in p.parts[1:]:
        require(part not in ('','.','..') and not part.endswith((' ','.')) and
                not any(c in part for c in ':<>"|?*'),'path-component')
        stem=part.split('.')[0].rstrip(' ').upper()
        require(stem not in ('CON','PRN','AUX','NUL','CONIN$','CONOUT$','CLOCK$') and
                re.fullmatch(r'(COM|LPT)[0-9¹²³]',stem) is None,'path-device')
    return p

def info(path,directory=False,missing=False):
    for part in [*reversed(path.parents),path]:
        try:s=part.lstat()
        except FileNotFoundError:
            if missing:return None
            raise
        require(not stat.S_ISLNK(s.st_mode) and
                not getattr(s,'st_file_attributes',0)&0x400,'path-redirect')
        require(stat.S_ISDIR(s.st_mode) if part!=path or directory else stat.S_ISREG(s.st_mode),
                'path-type')
    return s

def regular(s):
    return stat.S_ISREG(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400

def shared(s):
    return s.st_dev,s.st_ino,s.st_size

def stamp(s):
    # Compare only within one stat API: Windows path/fd ctime differ.
    return s.st_mtime_ns,s.st_ctime_ns,getattr(s,'st_birthtime_ns',None)

class Inputs:
    """Finite fixed-scope caller; byte budget includes bootstrap and final reads/writes."""
    def __init__(self):
        self.read_bytes=self.write_bytes=0
        self.seen={};self.caps={};self.paths={};self.directories={}
        self.tool=None
        self.reads=self

    @property
    def total(self):return self.read_bytes+self.write_bytes

    def pin_directory(self,path):
        path=path_value(path)
        for part in [*reversed(path.parents),path]:
            identity=shared(info(part,directory=True))[:2]
            require(part in self.directories or len(self.directories)<MAX_INPUTS*3,
                    'directory-count')
            require(part not in self.directories or self.directories[part]==identity,
                    'directory-identity-changed')
            self.directories[part]=identity

    def unchanged_directories(self):
        for path,identity in self.directories.items():
            require(shared(info(path,directory=True))[:2]==identity,'directory-identity-changed')

    def read(self,path,cap=65536,keep=True):
        path=path_value(path);self.pin_directory(path.parent);key=str(path).casefold()
        require(type(cap) is int and 0<cap<=MAX_FILE,'read-cap')
        require(key not in self.paths or self.paths[key]==path,'input-alias')
        require(key in self.paths or len(self.paths)<MAX_INPUTS,'input-count')
        cap=min(cap,self.caps.get(path,cap))
        before=info(path)
        require(0<=before.st_size<=cap and before.st_size<=MAX_IO-self.total,'read-budget')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
        try:
            opened=os.fstat(fd)
            require(regular(opened) and shared(opened)==shared(before),'read-open-identity')
            count=0;h=hashlib.sha256();chunks=[]
            while count<before.st_size:
                n=min(65536,before.st_size-count,MAX_IO-self.total)
                require(n>0,'read-budget')
                b=os.read(fd,n);self.read_bytes+=len(b)
                require(0<len(b)<=n and self.total<=MAX_IO,'read-short-or-limit')
                count+=len(b);h.update(b)
                if keep:chunks.append(b)
            after=os.fstat(fd)
            require(regular(after) and shared(after)==shared(opened) and stamp(after)==stamp(opened),
                    'read-handle-changed')
        finally:os.close(fd)
        self.pin_directory(path.parent)
        final=info(path)
        require(shared(final)==shared(before) and stamp(final)==stamp(before),'read-path-changed')
        record=(shared(final),stamp(final),h.hexdigest())
        require(path not in self.seen or self.seen[path]==record,'read-repeated-changed')
        self.paths[key]=path;self.caps[path]=cap;self.seen[path]=record
        return b''.join(chunks) if keep else (count,h.hexdigest())

    def json(self,path):return self.tool.manifest_json(self.read(path))

    def unchanged(self):
        self.unchanged_directories()
        for path,(identity,times,_) in self.seen.items():
            s=info(path)
            require((shared(s),stamp(s))==(identity,times),'input-final-stat')

    def recheck(self):
        self.unchanged()
        for path,cap in list(self.caps.items()):self.read(path,cap,keep=False)
        self.unchanged()

    def input_digest(self):
        return sha(encoded({str(p):{'bytes':v[0][2],'sha256':v[2]}
                            for p,v in sorted(self.seen.items(),key=lambda x:str(x[0]))}))

    def write_report(self,path,raw,companion):
        require(0<len(raw)<=MAX_REPORT and 3*len(raw)<=MAX_IO-self.total,'report-budget')
        self.pin_directory(path.parent)
        parent=info(path.parent,directory=True);parent_id=shared(parent)[:2]
        companion.require_new_name(path.parent,path.name)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|
                   getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            first=os.fstat(fd)
            require(regular(first) and first.st_size==0,'report-open-type')
            offset=0
            while offset<len(raw):
                n=os.write(fd,raw[offset:offset+65536])
                require(0<n<=min(65536,len(raw)-offset),'report-write')
                self.write_bytes+=n;offset+=n
                require(self.total<=MAX_IO,'report-budget')
            last=os.fstat(fd)
            require(regular(last) and last.st_size==len(raw) and
                    shared(first)[:2]==shared(last)[:2],'report-handle')
        finally:os.close(fd)
        require(shared(info(path))==shared(last) and
                shared(info(path.parent,directory=True))[:2]==parent_id,'report-path')
        self.unchanged_directories()
        require(self.read(path,MAX_REPORT)==raw,'report-readback')


def load_verified(inputs,path,size,digest,name):
    raw=inputs.read(path,128*1024)
    require(len(raw)==size and sha(raw)==digest,'tool-pin')
    module=types.ModuleType(name);module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module,{'bytes':len(raw),'sha256':sha(raw)}


def scan_bundle(tool,root,inputs):
    """Reuse pinned safe path rules, also reject directory case aliases."""
    files=set();directories=set();folded=set();stack=[root];nodes=0
    inputs.pin_directory(root)
    while stack:
        parent=stack.pop()
        with os.scandir(tool.no_redirect(parent)) as entries:
            for entry in entries:
                nodes+=1;require(nodes<=tool.MAX_FILES*2,'bundle-node-count')
                p=path_value(entry.path);tool.no_redirect(p)
                rel=tool.relative(p.relative_to(root).as_posix());key=rel.casefold()
                require(key not in folded,'bundle-case-alias');folded.add(key)
                s=p.lstat()
                if stat.S_ISDIR(s.st_mode):
                    require(len(p.relative_to(root).parts)<=tool.MAX_PATH_PARTS,'bundle-depth')
                    inputs.pin_directory(p)
                    directories.add(rel);stack.append(p)
                else:
                    require(regular(s) and len(files)<tool.MAX_FILES,'bundle-leaf-type-count')
                    files.add(rel)
    inputs.unchanged_directories()
    require(tool.candidate_files(root)==files,'bundle-parser-set')
    return files,directories


def payload_layout(raw,rows,start,end,target):
    """Inputs have already passed pinned range/overlap validation."""
    ordered=sorted((r for r in rows if r['stored_bytes']),key=lambda r:r['offset'])
    cursor=start;gaps=[]
    for row in ordered:
        off=row['offset'];require(start<=off and cursor<=off,'layout-offset')
        gaps.append(raw[cursor:off]);cursor=off+row['stored_bytes']
    require(cursor<=end,'layout-end');gaps.append(raw[cursor:end])
    return ordered,gaps


def compare_layout(a,b,rows_a,rows_b,start_a,end_a,start_b,end_b,target):
    aa,ga=payload_layout(a,rows_a,start_a,end_a,target)
    bb,gb=payload_layout(b,rows_b,start_b,end_b,target)
    require([r['name'] for r in aa]==[r['name'] for r in bb] and ga==gb,'payload-order-or-gap')
    ta=next(r for r in rows_a if r['name']==target)
    tb=next(r for r in rows_b if r['name']==target)
    delta=tb['stored_bytes']-ta['stored_bytes'];at=ta['offset'];old_end=at+ta['stored_bytes']
    require(tb['offset']==at and start_a==start_b and end_b==end_a+delta,'target-layout-delta')
    by_name={r['name']:r for r in rows_b}
    for row in rows_a:
        pos=row['offset']
        require(pos<=at or pos>=old_end,'unsupported-interior-offset')
        expected=pos if pos<=at else pos+delta
        require(by_name[row['name']]['offset']==expected,'unexplained-offset')
    return {'delta_bytes':delta,'gap_count':len(ga),'gap_bytes':sum(map(len,ga)),
            'physical_order_sha256':sha(encoded([r['name'] for r in aa]))}


def traced_pyz_toc(tool,data,rows):
    offset=struct.unpack('!i',data[8:12])[0]
    raw=data[offset:]
    class Traced(tool.TocData):
        def __init__(self,body):
            super().__init__(body);self.frames=[];self.spans={}
        def object(self,depth=0):
            if self.frames:
                parent=self.frames[-1];path=parent[0]+(parent[1],);parent[1]+=1
            else:path=()
            start=self.pos;self.frames.append([path,0])
            result=super().object(depth)
            self.frames.pop();self.spans[path]=(start,self.pos,result[0])
            return result
    traced=Traced(raw);table=traced.parse()
    require(len(table)==len(rows),'toc-trace-count')
    normalized=bytearray(raw)
    for i,row in enumerate(rows):
        fields=[1]+([2] if row['name']=='PySide6' else [])
        for field in fields:
            span=traced.spans.get((i,1,field))
            require(span is not None,'unsupported-numeric-reference')
            start,end,value=span
            require(end-start==5 and raw[start]&127==ord('i'),'unsupported-numeric-reference')
            expected=row['offset'] if field==1 else row['stored_bytes']
            require(type(value) is int and value==expected,'toc-trace-value')
            normalized[start+1:end]=b'\0'*4
    return bytes(normalized)


def compare_pyz(tool,a,b,rows_a,rows_b):
    require([(r['name'],r['kind']) for r in rows_a]==[(r['name'],r['kind']) for r in rows_b],
            'embedded-module-set-order-kind')
    targets=[r for r in rows_a if r['name'].casefold()=='pyside6']
    require(len(targets)==1 and targets[0]['name']=='PySide6' and targets[0]['kind']=='package',
            'embedded-target')
    require([r for r in rows_b if r['name'].casefold()=='pyside6'][0]['kind']=='package','embedded-target')
    for x,y in zip(rows_a,rows_b):
        if x['name']=='PySide6':require(x['stored_sha256']!=y['stored_sha256'],'target-unchanged')
        else:require((x['stored_bytes'],x['stored_sha256'])==(y['stored_bytes'],y['stored_sha256']),
                     'embedded-nontarget-changed')
    require(a[:8]==b[:8] and a[12:17]==b[12:17],'pyz-header')
    require(traced_pyz_toc(tool,a,rows_a)==traced_pyz_toc(tool,b,rows_b),'pyz-raw-toc')
    return compare_layout(a,b,rows_a,rows_b,17,struct.unpack('!i',a[8:12])[0],
                          17,struct.unpack('!i',b[8:12])[0],'PySide6')


def archive_parts(tool,exe,rows):
    cookie=tool.COOKIE.unpack(exe[-tool.COOKIE.size:])
    start=len(exe)-cookie[1];toc_start=start+cookie[2];toc=exe[toc_start:-tool.COOKIE.size]
    normalized=bytearray(toc);position=0
    while position<len(toc):
        size,off,clen,ulen,compressed,kind=tool.ENTRY.unpack(toc[position:position+tool.ENTRY.size])
        # The pinned parser already validated row size/name/padding/type/bounds.
        require(size>=tool.ENTRY.size and position+size<=len(toc),'toc-postvalidation')
        if kind!=b'o':normalized[position+4:position+8]=b'\0'*4
        if kind==b'z':normalized[position+8:position+16]=b'\0'*8
        position+=size
    require(position==len(toc),'toc-postvalidation')
    normalized_cookie=bytearray(exe[-tool.COOKIE.size:]);normalized_cookie[8:16]=b'\0'*8
    payload=exe[start:toc_start]
    return exe[:start],payload,bytes(normalized),bytes(normalized_cookie),cookie


def compare_archives(tool,a,b,inv_a,inv_b):
    rows_a,options_a,pyz_a,mods_a=inv_a;rows_b,options_b,pyz_b,mods_b=inv_b
    require(options_a==options_b and [r['name'] for r in rows_a]==[r['name'] for r in rows_b],
            'archive-name-order-options')
    for x,y in zip(rows_a,rows_b):
        keys=('name','type','compressed') if x['name']=='PYZ.pyz' else \
             ('name','type','compressed','stored_bytes','declared_uncompressed_bytes','stored_sha256')
        require(all(x[k]==y[k] for k in keys),'archive-nontarget-metadata')
    pa,payload_a,toc_a,cookie_a,ca=archive_parts(tool,a,rows_a)
    pb,payload_b,toc_b,cookie_b,cb=archive_parts(tool,b,rows_b)
    require(toc_a==toc_b and cookie_a==cookie_b,'archive-raw-toc-cookie')
    archive_layout=compare_layout(payload_a,payload_b,rows_a,rows_b,0,len(payload_a),0,len(payload_b),'PYZ.pyz')
    pyz_layout=compare_pyz(tool,pyz_a,pyz_b,mods_a,mods_b)
    return pa,pb,archive_layout,pyz_layout

PREFIX_POLICY='pe32plus-checksum-only-r1-proposal'

def pe_checksum(raw,offset):
    """16-bit little-endian checksum, excluding precisely the validated DWORD."""
    require(offset%4==0 and 0<=offset<=len(raw)-4,'checksum-offset')
    total=0
    for at in range(0,len(raw)-1,2):
        if offset<=at<offset+4:continue
        total+=raw[at]|(raw[at+1]<<8)
        total=(total&65535)+(total>>16)
    if len(raw)%2:
        total+=raw[-1];total=(total&65535)+(total>>16)
    total=(total&65535)+(total>>16)
    return total+len(raw)


def pe_prefix(raw,prefix,epoch):
    require(type(epoch) is int and 1<=epoch<=0xfffffffe,'epoch')
    require(64<=len(prefix)<=len(raw)<=MAX_FILE and prefix[:2]==b'MZ','pe-prefix')
    pe=struct.unpack_from('<I',prefix,60)[0]
    require(pe>=64 and pe%4==0 and pe+24+240<=len(prefix),'pe-header-bounds')
    require(prefix[pe:pe+4]==b'PE\0\0','pe-signature')
    machine,count,timestamp=struct.unpack_from('<HHI',prefix,pe+4)
    optional_size=struct.unpack_from('<H',prefix,pe+20)[0]
    require(machine==0x8664 and 1<=count<=96 and timestamp==epoch and optional_size==240,
            'pe-machine-section-epoch')
    opt=pe+24;section_table=opt+optional_size;section_end=section_table+count*40
    require(section_end<=len(prefix) and struct.unpack_from('<H',prefix,opt)[0]==0x20b,
            'pe32plus-header')
    require(struct.unpack_from('<I',prefix,opt+108)[0]==16,'pe-directory-count')
    headers=struct.unpack_from('<I',prefix,opt+60)[0]
    require(section_end<=headers<=len(prefix),'pe-header-extent')
    require(prefix[opt+112+4*8:opt+112+5*8]==b'\0'*8,'pe-certificate')
    file_align,section_align=struct.unpack_from('<I',prefix,opt+36)[0],struct.unpack_from('<I',prefix,opt+32)[0]
    require(512<=file_align<=65536 and file_align&(file_align-1)==0 and
            section_align>=file_align and section_align&(section_align-1)==0 and
            (section_align>=4096 or section_align==file_align) and headers%file_align==0,
            'pe-alignment')
    spans=[];virtual=[];mappings=[]
    for i in range(count):
        at=section_table+i*40
        vsize,rva,size,offset=struct.unpack_from('<IIII',prefix,at+8)
        extent=max(vsize,size)
        require(rva%section_align==0 and (not extent or rva>=headers) and
                extent<=0x100000000-rva,'pe-virtual-bounds')
        if extent:virtual.append((rva,extent))
        if size:
            require(offset%file_align==0 and size%file_align==0,'pe-raw-alignment')
            mappings.append((rva,size,offset))
            require(headers<=offset<=len(prefix) and size<=len(prefix)-offset,'pe-section-bounds')
            spans.append((offset,size))
    end=headers
    for offset,size in sorted(spans):
        require(offset>=end,'pe-section-overlap');end=offset+size
    require(end==len(prefix),'pe-unexplained-prefix-trailer')
    vend=0
    for rva,size in sorted(virtual):
        require(rva>=vend,'pe-virtual-overlap');vend=rva+size
    def map_rva(address,size):
        require(size>0 and size<=0x100000000-address,'pe-debug-range')
        matches=[raw+address-rva for rva,length,raw in mappings
                 if rva<=address and address-rva<=length and size<=length-(address-rva)]
        require(len(matches)==1,'pe-debug-mapping')
        return matches[0]
    debug_rva,debug_size=struct.unpack_from('<II',prefix,opt+112+6*8)
    require((debug_rva==0)==(debug_size==0),'pe-debug-pair')
    if debug_size:
        require(debug_size%28==0 and debug_size<=28*128,'pe-debug-directory-size')
        debug_at=map_rva(debug_rva,debug_size)
        for at in range(debug_at,debug_at+debug_size,28):
            debug_timestamp=struct.unpack_from('<I',prefix,at+4)[0]
            size,address,pointer=struct.unpack_from('<III',prefix,at+16)
            require(debug_timestamp in (0,epoch),'pe-debug-timestamp')
            if size:
                require(headers<=pointer<=len(prefix) and size<=len(prefix)-pointer,
                        'pe-debug-data-bounds')
                require(any(raw<=pointer and pointer-raw<=length and size<=length-(pointer-raw)
                            for _,length,raw in mappings),'pe-debug-data-coverage')
                if address:require(map_rva(address,size)==pointer,'pe-debug-address-pointer')
            else:require(address==0 and pointer==0,'pe-debug-empty-data')
    checksum_at=opt+64
    require(checksum_at+4<=opt+optional_size and checksum_at+4<=len(prefix),'pe-checksum-bounds')
    stored=struct.unpack_from('<I',prefix,checksum_at)[0]
    computed=pe_checksum(raw,checksum_at)
    require(stored==computed,'pe-checksum')
    normalized=bytearray(prefix);normalized[checksum_at:checksum_at+4]=b'\0'*4
    return bytes(normalized),{'bytes':len(prefix),'raw_sha256':sha(prefix),
          'normalized_sha256':sha(normalized),'checksum_offset':checksum_at,
          'stored_checksum':stored,'computed_checksum':computed,'coff_timestamp':timestamp}


def compare_prefix(a,b,pa,pb,epoch):
    na,ra=pe_prefix(a,pa,epoch);nb,rb=pe_prefix(b,pb,epoch)
    require(ra['checksum_offset']==rb['checksum_offset'] and na==nb,'prefix-nonchecksum-change')
    return {'policy':PREFIX_POLICY,'source_date_epoch':epoch,'control':ra,'modified':rb,
            'qualification':'Normalized equality only; not raw byte identity, authenticity or build correspondence'}


def byte_record(raw):return {'bytes':len(raw),'sha256':sha(raw)}

def path_record(inputs,path,cap):
    size,digest=inputs.read(path,cap,keep=False)
    return {'bytes':size,'sha256':digest}


def fixed_trial(tool,comp,root,inputs):
    for relative in ('','preparation','control','modified','control/bundle','modified/bundle',
                     'control/receipts','modified/receipts',
                     'control/bundle/replacement-probe','modified/bundle/replacement-probe'):
        inputs.pin_directory(root/relative)
    comp.fixed_layout(tool,root,(),('preparation','control','modified'))
    comp.exact_directory(tool,tool.child(root,'preparation'),comp.PREP_FILES)
    for variant in comp.VARIANTS:
        base=tool.child(root,variant)
        comp.fixed_layout(tool,base,('PYZ-00.pyz','build.log','source-inputs.json'),('bundle','receipts'))
        comp.exact_directory(tool,tool.child(base,'receipts'),())
        comp.exact_directory(tool,tool.child(base,'bundle/replacement-probe'),())


def compare(config):
    paths={k:path_value(config[k]) for k in ('self','inspector','companion','pins','source_zip',
                                           'build_record','trial','output')}
    root=paths['trial'];output=paths['output']
    require(len({str(p).casefold() for p in paths.values()})==len(paths),'argument-alias')
    for role,path in paths.items():
        if role not in ('trial','output'):
            require(root not in path.parents and path not in root.parents,'trial-input-overlap')
    forbidden=[root]+[p.parent for k,p in paths.items() if k not in ('trial','output')]
    require(all(output!=p and p not in output.parents and output not in p.parents for p in forbidden),
            'output-input-overlap')
    info(output.parent,directory=True)
    require(info(output,missing=True) is None,'output-exists')
    inputs=Inputs()
    inputs.pin_directory(output.parent)
    own=inputs.read(paths['self'],128*1024)
    tool,tool_id=load_verified(inputs,paths['inspector'],53390,INSPECTOR_HASH,'_pinned_inspector')
    comp,comp_id=load_verified(inputs,paths['companion'],34277,COMPANION_HASH,'_pinned_companion')
    inputs.tool=tool
    pins_raw=inputs.read(paths['pins'],65536)
    require(sha(pins_raw)==PINS_HASH,'pins-body')
    pins=tool.manifest_json(pins_raw)
    record_raw=inputs.read(paths['build_record'],65536);record=tool.manifest_json(record_raw)
    require(type(record) is dict and type(record.get('source_date_epoch')) is int and
            1<=record['source_date_epoch']<=0xfffffffe and record.get('prefix_policy')==PREFIX_POLICY,
            'record-prefix-policy')
    epoch=record['source_date_epoch']
    comp.source_identity(inputs,paths['source_zip'])
    fixed_trial(tool,comp,root,inputs)
    prep=comp.read_preparation(inputs,tool.child(root,'preparation'))
    executables={};archive_data={};sources={};inventories={};directory_sets={}
    build_bindings={};observations={};work_tables={}
    for variant in comp.VARIANTS:
        base=tool.child(root,variant);bundle=tool.child(base,'bundle')
        files,dirs=scan_bundle(tool,bundle,inputs)
        require('Motortester.exe' in files and all(Path(n).suffix.lower()!='.exe' or
                n=='Motortester.exe' for n in files),'bundle-executable-set')
        native=[n for n in files if Path(n).suffix.lower() in ('.dll','.pyd')]
        require(0<len(native)<=256 and all(n.startswith('_internal/') for n in native),
                'bundle-native-set')
        exe=inputs.read(tool.child(bundle,'Motortester.exe'),MAX_FILE)
        work=inputs.read(tool.child(base,'PYZ-00.pyz'),MAX_FILE)
        inv=tool.archive_inventory(exe,MAGIC);work_modules=tool.pyz_inventory(work,MAGIC)
        modules={r['name']:(r['kind'],r['stored_sha256']) for r in inv[3]}
        target=[r for r in inv[3] if r['name'].casefold()=='pyside6']
        require(len(target)==1 and target[0]['name']=='PySide6' and target[0]['kind']=='package',
                'target-package')
        init=prep['files']['original-init.py' if variant=='control' else 'modified-init.py']
        expected_source_hash=record['builds'][variant]['source_inputs']['sha256']
        source=comp.source_input_table(inputs,tool.child(base,'source-inputs.json'),modules,
                                      init['sha256'],init['bytes'],expected_source_hash)
        inventory={}
        for name in sorted(files):
            inventory[name]=byte_record(exe) if name=='Motortester.exe' else \
                            path_record(inputs,tool.child(bundle,name),MAX_FILE)
        log=path_record(inputs,tool.child(base,'build.log'),2*MIB)
        require(log['bytes']>0,'empty-build-log')
        source_path=tool.child(base,'source-inputs.json')
        source_bound={'bytes':inputs.seen[source_path][0][2],'sha256':inputs.seen[source_path][2]}
        build_bindings[variant]={'initializer':init,'source_inputs':source_bound,'build_log':log,
            'executable':byte_record(exe),'work_pyz':byte_record(work),
            'bundle_inventory_sha256':sha(comp.encoded(inventory)),
            'source_tree_unchanged':True,'changed_inputs':[] if variant=='control' else [comp.MEMBER],
            'build_succeeded':True}
        observations[variant]={'exe':byte_record(exe),'embedded_pyz':byte_record(inv[2]),
            'work_pyz':byte_record(work),'work_matches_embedded':work==inv[2],
            'work_module_count':len(work_modules),'work_inventory_sha256':sha(comp.encoded(work_modules)),
            'embedded_module_count':len(inv[3]),'bundle_leaf_count':len(files),'native_leaf_count':len(native),
            'bundle_inventory_sha256':sha(comp.encoded(inventory)),
            'bundle_directory_set_sha256':sha(comp.encoded(sorted(dirs)))}
        executables[variant]=exe;archive_data[variant]=inv;sources[variant]=source
        inventories[variant]=inventory;directory_sets[variant]=(files,dirs);work_tables[variant]=work_modules
    a,b=comp.VARIANTS
    require(directory_sets[a]==directory_sets[b],'bundle-set-change')
    require(all(inventories[a][n]==inventories[b][n] for n in inventories[a] if n!='Motortester.exe'),
            'runtime-leaf-change')
    require(set(sources[a])==set(sources[b]) and
            all(tool.same_json(sources[a][n],sources[b][n]) for n in sources[a] if n!='PySide6'),
            'source-observation-change')
    require(executables[a]!=executables[b],'executable-unchanged')
    pa,pb,archive_layout,pyz_layout=compare_archives(tool,executables[a],executables[b],
                                                    archive_data[a],archive_data[b])
    prefix=compare_prefix(executables[a],executables[b],pa,pb,epoch)
    expected={'schema_version':1,'scope':'wrapper-declared-prelaunch-inputs','trial_id':prep['trial_id'],
        'application':{'commit':comp.APP,'tree':comp.TREE},
        'source_zip':{'bytes':comp.SOURCE_BYTES,'sha256':comp.SOURCE_SHA},
        'tools':{'inspector':tool_id,'companion':comp_id,'comparator':byte_record(own)},
        'app_inputs':pins['app_inputs'],'wheels':pins['wheels'],
        'runtime':{'platform':'win32','python':'3.13.16','machine':'AMD64','free_threaded':False,
                   'python_magic':MAGIC.hex()},'source_date_epoch':epoch,'prefix_policy':PREFIX_POLICY,
        'builds':build_bindings,'verified_by_comparator':{'wheel_bodies':False,'runtime':False,
                                                       'build_execution':False,'source_to_bytecode':False}}
    require(tool.same_json(record,expected),'closed-build-record-mismatch')
    wa={r['name']:(r['kind'],r['stored_bytes'],r['stored_sha256']) for r in work_tables[a]}
    wb={r['name']:(r['kind'],r['stored_bytes'],r['stored_sha256']) for r in work_tables[b]}
    differences={'added':sorted(set(wb)-set(wa)),'removed':sorted(set(wa)-set(wb)),
                 'changed':sorted(n for n in set(wa)&set(wb) if wa[n]!=wb[n])}
    work_observation={'counts':{k:len(v) for k,v in differences.items()},
                      'complete_difference_sha256':sha(comp.encoded(differences)),
                      'first_16_names':{k:v[:16] for k,v in differences.items()},
                      'qualification':'Work PYZ differences are observed, not authoritative or explained'}
    inputs.recheck()
    fixed_trial(tool,comp,root,inputs)
    for variant in comp.VARIANTS:
        require(scan_bundle(tool,tool.child(root,variant+'/bundle'),inputs)==directory_sets[variant],
                'bundle-final-set')
    binding=inputs.input_digest()
    result={'schema_version':1,'scope':'prelaunch-byte-comparison-only',
            'status':'UNCOMMITTED_PRELAUNCH_COMPARISON','trial_id':prep['trial_id'],
            'application_commit':comp.APP,'application_tree':comp.TREE,
            'build_record':byte_record(record_raw),'inspected_input_set_sha256':binding,
            'inspected_input_count':len(inputs.seen),'variants':observations,
            'carchive_layout':archive_layout,'embedded_pyz_layout':pyz_layout,
            'prefix_comparison':prefix,'work_comparison':work_observation,
            'only_initializer_source_observation_changed':True,
            'only_target_stored_module_changed':True,'all_other_bundle_leaves_identical':True,
            'wrapper_recheck_before_each_child_required':True,
            'declared_build_inputs_are_not_verified_execution':True,
            **{k:False for k in FALSE_FIELDS}}
    raw=comp.encoded(result);require(len(raw)<=MAX_REPORT,'report-size')
    inputs.write_report(output,raw,comp)
    inputs.unchanged();fixed_trial(tool,comp,root,inputs)
    for variant in comp.VARIANTS:
        require(scan_bundle(tool,tool.child(root,variant+'/bundle'),inputs)==directory_sets[variant],
                'bundle-postwrite-set')
    require(inputs.read(output,MAX_REPORT)==raw,'final-report-bytes')
    return {'schema_version':1,'status':'PRELAUNCH_BYTES_MATCH','report_bytes':len(raw),
            'report_sha256':sha(raw),'inspected_input_set_sha256':binding,
            'read_bytes':inputs.read_bytes,'write_bytes':inputs.write_bytes,
            'managed_bytes':inputs.total,'managed_limit':MAX_IO,
            'report_is_not_execution_authorization':True,**{k:False for k in FALSE_FIELDS}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('inspector','companion','pins','source-zip','build-record','trial','output'):
        parser.add_argument('--'+arg,required=True)
    args=vars(parser.parse_args(argv))
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'isolated-python-required')
    args['self']=str(Path(__file__).absolute())
    receipt=compare(args)
    print(json.dumps(receipt,sort_keys=True,allow_nan=False))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Rejected as error:
        print('PRELAUNCH_REJECT '+error.code,file=sys.stderr);raise SystemExit(1)
    except Exception:
        print('PRELAUNCH_REJECT external-input-or-io',file=sys.stderr);raise SystemExit(1)
