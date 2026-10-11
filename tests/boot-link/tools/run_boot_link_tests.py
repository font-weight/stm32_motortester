#!/usr/bin/env python3
"""BOOT001 object/link/ELF evidence, never target execution or product closure."""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys

RESET_SHA = '73aab7ae43f89544fb2d6b7a931ff1a42a1ad4034c57e5256453dd04b51f1e18'
LINK_SHA = '54a13bae7a9edc25ca38dadaa3c31a957108099fbb193c66d3e32dcc26b7d60c'
VECTOR_SHA = 'c081969e7a2d7243f5cffde1a1937e5fd799b4c798d2ad627ffd1af20dafac2c'
FLASH, FLASH_END, RAM, RAM_END = 0x08000000, 0x08010000, 0x20000000, 0x20005000
ASM_HEADER = '.syntax unified\n.cpu cortex-m3\n.thumb\n'
ASM_FOOTER = '\n.section .note.GNU-stack,"",%progbits\n'
MAIN = ASM_HEADER + '''.section .text.main,"ax",%progbits
.global main
.type main,%function
.thumb_func
main:
movs r0,#0
bx lr
.size main,.-main
''' + ASM_FOOTER


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_path(value, kind='file', executable=False):
    """Reject lexical traversal, symlink aliases, nonregular backing inputs."""
    raw = os.fspath(value)
    p = Path(raw)
    require(p.is_absolute() and os.path.normpath(raw) == raw, 'absolute normalized path required: ' + raw)
    for part in [*reversed(p.parents), p]:
        require(not part.is_symlink(), 'symlink component forbidden: ' + str(part))
    require(p.resolve(strict=True) == p, 'canonical backing path required: ' + raw)
    st = p.stat()
    require(stat.S_ISREG(st.st_mode) if kind == 'file' else stat.S_ISDIR(st.st_mode), 'wrong input type: ' + raw)
    if executable:
        require(os.access(p, os.X_OK), 'not executable: ' + raw)
    return p


def validate_output(value, protected_directories):
    raw=os.fspath(value);out=Path(raw)
    require(out.is_absolute() and os.path.normpath(raw)==raw,'output must be absolute normalized path')
    require(not out.exists() and not out.is_symlink(),'output must not exist')
    strict_path(str(out.parent),'dir')
    for inp in protected_directories:
        require(not inside(out,inp) and not inside(inp,out),'output overlaps protected input directory: '+str(inp))
    return out


def inside(p, q):
    return p == q or q in p.parents


class ELF:
    """Small dependency-free ELF32 little-endian reader with bounds checks."""
    def __init__(self, path):
        self.path = path
        self.raw = path.read_bytes()
        require(self.raw[:7] == b'\x7fELF\x01\x01\x01', 'not little-endian ELF32')
        h = self.unpack('<HHIIIIIHHHHHH', 16)
        self.type, self.machine, _, self.entry, phoff, shoff, self.flags, ehsize, phsize, phnum, shsize, shnum, shstr = h
        require(self.machine == 40 and ehsize == 52 and shsize == 40, 'not supported ARM ELF')
        require(shnum and shstr < shnum, 'unsupported extended section numbering')
        self.shoff=shoff
        self.shsize=shsize
        self.sections = []
        for i in range(shnum):
            v = self.unpack('<10I', shoff + i * shsize)
            s = dict(zip(('name_offset','type','flags','addr','offset','size','link','info','align','entsize'), v))
            s['index'] = i
            self.sections.append(s)
        strings = self.bytes(self.sections[shstr])
        for s in self.sections:
            s['name'] = self.string(strings, s['name_offset'])
        self.by_name = {s['name']:s for s in self.sections}
        self.symbols = {}
        self.symbol_tables = {}
        self.undefined = []
        for s in self.sections:
            if s['type'] != 2:
                continue
            require(s['entsize'] == 16 and s['size'] % 16 == 0, 'bad symbol table')
            names = self.bytes(self.sections[s['link']])
            self.symbol_tables[s['index']]=[]
            for off in range(s['offset'], s['offset'] + s['size'], 16):
                name, value, size, info, other, ndx = self.unpack('<IIIBBH', off)
                name = self.string(names, name)
                sym = dict(value=value,size=size,bind=info>>4,type=info&15,visibility=other&3,ndx=ndx)
                self.symbol_tables[s['index']].append((name,sym))
                if name:
                    self.symbols[name] = sym
                    if ndx == 0:
                        self.undefined.append(name)
        self.segments = []
        require(phnum == 0 or phsize == 32, 'bad program header size')
        for i in range(phnum):
            self.segments.append(dict(zip(('type','offset','vaddr','paddr','filesz','memsz','flags','align'), self.unpack('<8I', phoff + i*phsize))))

    def unpack(self, fmt, off):
        require(0 <= off and off + struct.calcsize(fmt) <= len(self.raw), 'ELF read out of bounds')
        return struct.unpack_from(fmt, self.raw, off)

    @staticmethod
    def string(raw, off):
        require(0 <= off < len(raw), 'string index out of bounds')
        end = raw.find(b'\0', off)
        require(end >= 0, 'unterminated ELF string')
        return raw[off:end].decode('ascii')

    def bytes(self, section):
        require(section['type'] != 8, 'NOBITS has no file bytes')
        a, n = section['offset'], section['size']
        require(a >= 0 and a + n <= len(self.raw), 'section out of bounds')
        return self.raw[a:a+n]

    def value(self, name):
        require(name in self.symbols, 'missing symbol ' + name)
        return self.symbols[name]['value']

    def word(self, addr):
        for s in self.sections:
            if s['type'] != 8 and s['addr'] <= addr and addr + 4 <= s['addr'] + s['size']:
                return self.unpack('<I', s['offset'] + addr - s['addr'])[0]
        raise AssertionError('no file-backed word at ' + hex(addr))


def check_vector_relocations(e, rows):
    v=e.by_name['.isr_vector']
    reloc=[]
    for sec in e.sections:
        if sec['type']!=9 or sec['info']!=v['index']:
            continue
        require(sec['entsize']==8 and sec['size']%8==0,'invalid vector relocation structure')
        symbols=e.symbol_tables[sec['link']]
        for off in range(sec['offset'],sec['offset']+sec['size'],8):
            address,info=e.unpack('<II',off)
            require(info&255==2,'vector relocation must be R_ARM_ABS32')
            reloc.append((address,symbols[info>>8][0]))
    expected=[(4*r['index'],'__StackTop' if r['index']==0 else r['vendor_word']) for r in rows if r['vendor_word']!='0']
    require(reloc==expected,'vector relocation order/name mismatch')
    require(v['size']==236 and v['align']==512,'object vector geometry')
    return dict(relocations=reloc,meaning='Exact symbol ordering established before all weak aliases share one linked address.')


def check_attributes(text):
    tags={}
    for line in text.splitlines():
        m=re.match(r'\s*(Tag_[A-Za-z0-9_]+):\s*(.*?)\s*$',line)
        if m:
            require(m[1] not in tags,'duplicate ARM attribute '+m[1])
            tags[m[1]]=m[2]
    require(tags.get('Tag_CPU_arch')=='v7','ARM CPU architecture must be v7')
    require(tags.get('Tag_CPU_arch_profile')=='Microcontroller','ARM CPU profile must be Microcontroller')
    require(tags.get('Tag_THUMB_ISA_use')=='Thumb-2','Thumb-2 attribute required')
    require(tags.get('Tag_ARM_ISA_use','No')=='No','ARM-state instructions unsupported')
    require(tags.get('Tag_ABI_enum_size','int')=='int','short enums unsupported')
    require(tags.get('Tag_ABI_PCS_wchar_t','4')=='4','non-32-bit wchar ABI unsupported')
    require(tags.get('Tag_ABI_VFP_args','AAPCS')=='AAPCS','VFP register argument ABI unsupported')
    require(not any(x in tags for x in ('Tag_FP_arch','Tag_Advanced_SIMD_arch','Tag_MVE_arch')),'floating/SIMD extension attributes unsupported')
    require(tags.get('Tag_ABI_align_needed','8-byte')=='8-byte','ABI stack alignment needed differs')
    require(tags.get('Tag_ABI_align_preserved','8-byte, except leaf SP') in ('8-byte','8-byte, except leaf SP'),'ABI alignment preservation differs')
    return dict(tags=tags,limits='Checks emitted attributes using pinned readelf, not every opcode, provenance, or board identity; assembler omissions of optional ABI attributes are allowed.')


def audit_input_metadata(e):
    require(e.flags&0xff000000==0x05000000 and not e.flags&0x400,'input ARM EABI5 soft-float flags required')
    require(e.type==1, 'input must be an ELF relocatable object; archives and LTO unsupported')
    checked=[]
    for s in e.sections:
        if s['name'] not in ('.comment','.note.GNU-stack','.ARM.attributes'):
            continue
        expected_type=0x70000003 if s['name']=='.ARM.attributes' else 1
        require(s['type']==expected_type, 'metadata type invalid: '+s['name'])
        require(not s['flags']&7, 'allocating/writable/executable metadata forbidden: '+s['name'])
        require(s['flags'] in ((0,48) if s['name']=='.comment' else (0,)), 'unexpected metadata flags: '+s['name'])
        if s['name']=='.note.GNU-stack':
            require(s['size']==0, 'nonempty stack metadata forbidden')
        checked.append(s)
    require(any(s['name']=='.ARM.attributes' for s in checked), 'ARM attributes missing')
    return dict(path=str(e.path),sha256=digest(e.path),metadata=checked,policy='Pre-link metadata audit. All other input sections require supported no-GC link and final ELF validation.')


def executable_function(e, name, strong=False):
    s=e.symbols[name]
    require(s['type']==2 and s['value']&1 and s['bind'] in ((1,) if strong else (1,2)), name+' must be '+('strong ' if strong else '')+'Thumb function')
    require(0 < s['ndx'] < len(e.sections), name+' must belong to a real section')
    sec=e.sections[s['ndx']]
    addr=s['value']&~1
    require(sec['type']==1 and sec['flags']&6==6 and not sec['flags']&1, name+' requires executable read-only section')
    require(s['size']>0 and sec['addr'] <= addr < addr+s['size'] <= sec['addr']+sec['size'] and FLASH <= addr < addr+s['size'] <= FLASH_END, name+' function extent outside executable Flash')


def check_vectors(e, rows):
    v = e.by_name['.isr_vector']
    require(v['addr'] == FLASH and v['size'] == 236 and v['align'] >= 512, 'vector geometry')
    require(v['flags'] == 2 and v['type'] == 1, 'vector flags/type')
    sym = e.symbols['mt_boot_vectors']
    require(sym['value'] == FLASH and sym['size'] == 236 and sym['bind'] == 1 and sym['type'] == 1, 'vector symbol')
    words = list(struct.unpack('<59I', e.bytes(v)))
    for row, word in zip(rows, words):
        i = row['index']
        if i == 0:
            require(word == RAM_END and word % 8 == 0, 'initial MSP')
        elif row['vendor_word'] == '0':
            require(word == 0, 'reserved vector ' + str(i))
        else:
            executable_function(e,row['vendor_word'])
            s = e.symbols[row['vendor_word']]
            require(word == s['value'] and word & 1 and s['type'] == 2, 'handler/Thumb vector ' + str(i))
            require(FLASH <= (word & ~1) < FLASH_END, 'handler outside Flash')
    return words


def check_layout(e, stack=4096):
    require(e.type == 2 and e.flags & 0xff000000 == 0x05000000, 'EXEC/EABI5 required')
    require(not e.flags & 0x400, 'hard float ABI forbidden')
    require(not any(s['type'] in (4,9) and s['size'] for s in e.sections), 'unresolved output relocations')
    require(not e.undefined, 'undefined: ' + repr(e.undefined))
    for name in ('Reset_Handler','main','Default_Handler'):
        executable_function(e,name,strong=True)
    require(e.entry == e.value('Reset_Handler'), 'ELF entry')
    require(e.value('__StackTop') == RAM_END and e.value('__StackLimit') == RAM_END-stack, 'stack symbols')
    require(e.value('__static_end') <= e.value('__StackLimit'), 'static/stack overlap')
    vals = [e.value(n) for n in ('__data_start','__data_end','__data_load','__bss_start','__bss_end')]
    ds,de,dl,bs,be=vals
    data=e.by_name['.data'];bss=e.by_name['.bss']
    require((ds,de)==(data['addr'],data['addr']+data['size']), 'data symbols must equal section extent')
    require((bs,be)==(bss['addr'],bss['addr']+bss['size']), 'bss symbols must equal section extent')
    require(e.value('__static_end')==be,'static end must equal complete bss end')
    require(all(x%4 == 0 for x in vals), 'unaligned reset range')
    require(RAM <= ds <= de <= bs <= be <= e.value('__static_end') <= RAM_END, 'RAM bounds/order')
    require(FLASH <= dl <= dl + de-ds <= FLASH_END, 'data LMA bounds')
    alloc=[]
    spans=[]
    for s in e.sections:
        if s['flags'] & 2:
            alloc.append(s)
            if not s['size']:
                continue
            require(s['name'] in ('.isr_vector','.text','.data','.bss'), 'unexpected alloc ' + s['name'])
            require(s['flags']=={'.isr_vector':2,'.text':6,'.data':3,'.bss':3}[s['name']], 'unexpected section flags')
            a,b=s['addr'],s['addr']+s['size']
            if s['name'] in ('.isr_vector','.text'):
                require(FLASH <= a < b <= FLASH_END and not s['flags'] & 1, 'Flash section bounds/permissions')
            else:
                require(RAM <= a < b <= RAM_END and s['flags'] & 1 and not s['flags'] & 4, 'RAM section bounds/permissions')
            require((s['type'] == 8) == (s['name'] == '.bss'), 'NOBITS policy')
            spans.append((a,b,s['name']))
    for a,b in itertools.combinations(spans,2):
        require(a[1] <= b[0] or b[1] <= a[0], 'overlapping alloc sections')
    loads=[p for p in e.segments if p['type'] == 1]
    file_spans=[]
    for p in loads:
        require(p['filesz'] <= p['memsz'] and p['offset']+p['filesz'] <= len(e.raw), 'bad PT_LOAD sizes')
        require(not(p['flags'] & 1 and p['flags'] & 2), 'writable executable PT_LOAD')
        a,b=p['vaddr'],p['vaddr']+p['memsz']
        require(FLASH <= a <= b <= FLASH_END or RAM <= a <= b <= RAM_END, 'PT_LOAD VMA bounds')
        if p['filesz']:
            a,b=p['paddr'],p['paddr']+p['filesz']
            require(FLASH <= a < b <= FLASH_END, 'PT_LOAD LMA bounds')
            file_spans.append((a,b))
    for a,b in itertools.combinations(file_spans,2):
        require(a[1] <= b[0] or b[1] <= a[0], 'overlapping load bytes')
    for s in alloc:
        if s['size']:
            covering=[p for p in loads if p['vaddr'] <= s['addr'] and s['addr']+s['size'] <= p['vaddr']+p['memsz']]
            require(covering, 'alloc not covered by PT_LOAD')
            if s['type'] != 8:
                expected_lma=dl if s['name']=='.data' else s['addr']
                require(any(s['offset']-p['offset']==s['addr']-p['vaddr'] and p['paddr']+s['offset']-p['offset']==expected_lma and s['offset']+s['size'] <= p['offset']+p['filesz'] for p in covering), 'section file offset/LMA mismatch')
    return dict(alloc_sections=alloc, pt_load=loads, all_program_headers=e.segments, reset_ranges=dict(data_start=ds,data_end=de,data_load=dl,bss_start=bs,bss_end=be), stack_top=RAM_END, stack_limit=RAM_END-stack, static_end=e.value('__static_end'))


def instructions(disassembly, start, size):
    out=[]
    for line in disassembly.splitlines():
        m=re.match(r'^\s*([0-9a-f]+):\s+([0-9a-f]{4}(?:[ \t]+[0-9a-f]{4})?)[ \t]+([a-z][a-z0-9.]*)(?:[ \t]+(.*))?$',line)
        if m and start <= int(m[1],16) < start+size:
            out.append((int(m[1],16),m[3],(m[4] or '').split('@')[0].strip()))
    return out


def check_reset(e, disassembly):
    reset=e.symbols['Reset_Handler']
    ins=instructions(disassembly,reset['value']&~1,reset['size'])
    names=['cpsid','ldr','ldr','str','ldr','ldr','orr.w','str','dsb','isb','ldr','ldr','ldr','cmp','bcs.n','ldr.w','str.w','b.n','ldr','ldr','movs','cmp','bcs.n','str.w','b.n','dsb','bl','cpsid','b.n']
    require([i[1] for i in ins] == names, 'reset instruction sequence differs: '+repr(ins))
    expected={0:'i',3:'r1, [r0, #0]',5:'r1, [r0, #0]',6:'r1, r1, #512',7:'r1, [r0, #0]',8:'sy',9:'sy',13:'r1, r2',15:'r3, [r0], #4',16:'r3, [r1], #4',20:'r3, #0',21:'r1, r2',23:'r3, [r1], #4',25:'sy',27:'i'}
    for i,operand in expected.items():
        require(ins[i][2] == operand, 'reset operands at '+str(i))
    for src,dst in ((14,18),(17,13),(22,25),(24,21),(28,28)):
        require(int(ins[src][2].split()[0],16) == ins[dst][0], 'reset branch target')
    require(int(ins[26][2].split()[0],16) == e.value('main')&~1, 'only call must target main')
    literal={1:('r0',0xe000ed08),2:('r1',FLASH),4:('r0',0xe000ed14),10:('r0',e.value('__data_load')),11:('r1',e.value('__data_start')),12:('r2',e.value('__data_end')),18:('r1',e.value('__bss_start')),19:('r2',e.value('__bss_end'))}
    for i,(reg,val) in literal.items():
        m=re.match(r'(r[012]), \[pc, #(\d+)\]',ins[i][2])
        require(m and m[1] == reg, 'reset literal register')
        require(e.word(((ins[i][0]+4)&~3)+int(m[2])) == val, 'reset literal value')
    d=e.symbols['Default_Handler']
    default=instructions(disassembly,d['value']&~1,d['size'])
    require(len(default)==2 and default[0][1:]==('cpsid','i') and default[1][1]=='b.n' and int(default[1][2].split()[0],16)==default[1][0], 'default trap')
    return dict(reset_instructions=ins,default_instructions=default,validation='static machine-code disassembly; no ISA execution')


def model_ranges(e, dn, bn):
    """Native interval model, explicitly not execution of ARM reset."""
    ds,de,dl,bs,be=[e.value(n) for n in ('__data_start','__data_end','__data_load','__bss_start','__bss_end')]
    require(de-ds == (dn+3)//4*4 and be-bs == (bn+3)//4*4, 'input/padded sizes')
    raw=e.bytes(e.by_name['.data'])
    require(raw[:dn] == bytes([0x5a])*dn, 'data initializer bytes')
    require(raw[dn:] == bytes(len(raw)-dn), 'data padding bytes')
    memory=bytearray([0xa7])*(RAM_END-RAM+8)
    writes=[]
    for p in range(ds,de,4):
        memory[p-RAM+4:p-RAM+8]=raw[p-ds:p-ds+4]
        writes.extend(range(p,p+4))
    for p in range(bs,be,4):
        memory[p-RAM+4:p-RAM+8]=bytes(4)
        writes.extend(range(p,p+4))
    expected=set(range(ds,de))|set(range(bs,be))
    require(set(writes)==expected and len(writes)==len(expected), 'model exact-once exclusive writes')
    for i,value in enumerate(memory):
        addr=RAM+i-4
        if addr not in expected:
            require(value==0xa7,'model sentinel altered')
    require(memory[ds-RAM+4:de-RAM+4] == raw and memory[bs-RAM+4:be-RAM+4] == bytes(be-bs), 'model initialized ranges')
    return dict(input_data_bytes=dn,input_bss_bytes=bn,padded_data_bytes=de-ds,padded_bss_bytes=be-bs,padding_bytes=de-ds-dn+be-bs-bn,sentinel_value=0xa7,mode='native range model; ARM instructions not executed')


class Run:
    def __init__(self, root, tools, env, rows):
        self.root,self.tools,self.env,self.rows=root,tools,env,rows
        self.commands=[]
        self.results=[]
        self.counter=0

    def command(self, args, cwd):
        p=subprocess.run([str(x) for x in args],cwd=cwd,env=self.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        record=dict(argv=[str(x) for x in args],shell=shlex.join([str(x) for x in args]),cwd=str(cwd),returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
        self.commands.append(record)
        (self.root/'commands.json').write_text(json.dumps(self.commands,indent=2)+'\n')
        return p

    def compile(self, source, dest):
        flags=['-B'+str(self.tools['gcc'].parent)+'/arm-none-eabi-', '-mcpu=cortex-m3','-mthumb','-mfloat-abi=soft','-ffreestanding','-fno-common','-fno-short-enums','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-fno-stack-protector','-fno-lto','-fno-pic','-fno-pie','-ffunction-sections','-fdata-sections','-g0','-Os','-Wall','-Wextra','-Werror']
        p=self.command([self.tools['gcc'],*flags,'-c',source,'-o',dest],dest.parent)
        require(p.returncode==0,'fixture compilation failed: '+p.stderr)

    def case(self, name, expect=True, source=MAIN, extra='', reset=None, linker=None, stack=4096, retain=(), gc=False, diag=None, inspect=True):
        folder=self.root/name
        folder.mkdir()
        objects=[]
        if reset is not False:
            rp=self.root/'inputs/mt_reset_stm32f103c8.S'
            if reset is not None:
                rp=folder/'reset.S'; rp.write_text(reset)
            ro=folder/'reset.o'; self.compile(rp,ro); objects.append(ro)
        if source is not None:
            sp=folder/('fixture.c' if source.startswith('/* C fixture */') else 'fixture.S')
            sp.write_text(source); so=folder/'fixture.o'; self.compile(sp,so); objects.append(so)
        if extra:
            ep=folder/'extra.S';ep.write_text(ASM_HEADER+extra+ASM_FOOTER);eo=folder/'extra.o';self.compile(ep,eo);objects.append(eo)
        input_audit=[]
        for obj in objects:
            audit=audit_input_metadata(ELF(obj))
            attrs=self.command([self.tools['readelf'],'-A',obj],folder)
            require(attrs.returncode==0,'input readelf failed')
            audit['attributes']=check_attributes(attrs.stdout)
            input_audit.append(audit)
        (folder/'input_audit.json').write_text(json.dumps(input_audit,indent=2)+'\n')
        lp=self.root/'inputs/stm32f103c8.ld'
        if linker is not None:
            lp=folder/'script.ld';lp.write_text(linker)
        argv=[self.tools['ld']]
        if stack is not None:
            argv+=['--defsym=__boot_stack_bytes='+str(stack)]
        argv+=['-T',lp,'--orphan-handling=error','--print-gc-sections']
        argv+=['--gc-sections' if gc else '--no-gc-sections']
        for n in retain: argv+=['--undefined='+n]
        argv+=['-Map='+str(folder/'image.map'),*objects,'-o',folder/'image.elf']
        p=self.command(argv,folder)
        (folder/'link.stdout').write_text(p.stdout);(folder/'link.stderr').write_text(p.stderr)
        ok=(p.returncode==0)==expect and (not diag or any(s in p.stderr for s in diag))
        result=dict(name=name,expected='link_success' if expect else 'link_refusal',link_returncode=p.returncode,passed=ok,gc=gc,diagnostic_required=diag,errors=[])
        if not ok:result['errors'].append('unexpected link outcome/diagnostic')
        e=None
        if p.returncode==0 and inspect:
            try:
                e=ELF(folder/'image.elf')
                commands={'readelf':['-W','-h','-S','-l','-s','-r','-A'],'objdump':['-d','-r'],'nm':['-a','-S'],'size':['-A']}
                for tool,opts in commands.items():
                    t=self.command([self.tools[tool],*opts,folder/'image.elf'],folder)
                    require(t.returncode==0,'inspection tool failed')
                    (folder/(tool+'.txt')).write_text(t.stdout+t.stderr)
                result['elf_sha256']=digest(folder/'image.elf')
                result['layout']=check_layout(e,stack)
                result['attributes']=check_attributes((folder/'readelf.txt').read_text())
                result['vectors']=check_vectors(e,self.rows)
                result['reset']=check_reset(e,(folder/'objdump.txt').read_text())
            except (AssertionError,KeyError,ValueError) as exc:
                result['passed']=False; result['errors'].append(str(exc))
        self.results.append(result)
        (self.root/'results.json').write_text(json.dumps(self.results,indent=2)+'\n')
        print(name, 'PASS' if result['passed'] else 'FAIL', flush=True)
        return e,result


def cfixture(dn,bn):
    out='/* C fixture */\n'
    terms=[]
    if dn:
        out+=f'volatile unsigned char fixture_data[{dn}] __attribute__((section(".data.fixture"), aligned(1))) = {{'+','.join(['0x5a']*dn)+'};\n'
        terms+=['fixture_data[0]',f'fixture_data[{dn-1}]']
    if bn:
        out+=f'volatile unsigned char fixture_bss[{bn}] __attribute__((section(".bss.fixture"), aligned(1)));\n'
        terms+=['fixture_bss[0]',f'fixture_bss[{bn-1}]']
    return out+'int main(void) { return '+(' + '.join(terms) if terms else '0')+'; }\n'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--boot-dir',required=True)
    ap.add_argument('--vector-map',required=True)
    ap.add_argument('--tool-root',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--expected-reset-sha',default=RESET_SHA)
    ap.add_argument('--expected-linker-sha',default=LINK_SHA)
    args=ap.parse_args()
    # All user inputs and their backing paths are validated before creating output.
    boot=strict_path(args.boot_dir,'dir');vector=strict_path(args.vector_map);toolroot=strict_path(args.tool_root,'dir')
    sources={n:strict_path(str(boot/n)) for n in ('mt_reset_stm32f103c8.S','stm32f103c8.ld','README.md')}
    require(digest(sources['mt_reset_stm32f103c8.S'])==args.expected_reset_sha,'reset source SHA mismatch')
    require(digest(sources['stm32f103c8.ld'])==args.expected_linker_sha,'linker source SHA mismatch')
    require(digest(vector)==VECTOR_SHA,'vector map SHA mismatch')
    vector_bytes=vector.read_bytes()
    vector_data=json.loads(vector_bytes);rows=vector_data['rows'][:59]
    require(vector_data['flash_only_vector_words']==59 and len(rows)==59 and [x['index'] for x in rows]==list(range(59)),'vector map invalid')
    tools={n:strict_path(str(toolroot/'bin'/('arm-none-eabi-'+n)),executable=True) for n in ('gcc','as','ld','objdump','readelf','nm','size')}
    cc1=strict_path(str(toolroot/'lib/gcc/arm-none-eabi/14.2.1/cc1'),executable=True)
    tools['cc1']=cc1
    own=strict_path(str(Path(__file__).absolute()))
    validator=strict_path(str(own.parent/'check_boot_image.py'))
    out=validate_output(args.output,[boot,toolroot,vector.parent,own.parent])
    env={'PATH':str(toolroot/'bin')+':/usr/bin:/bin','LANG':'C','LC_ALL':'C','TZ':'UTC','SOURCE_DATE_EPOCH':'1791662400'}
    versions={}
    for name in ('gcc','as','ld','objdump','readelf','nm','size'):
        p=subprocess.run([str(tools[name]),'--version'],env=env,text=True,capture_output=True)
        require(p.returncode==0,'tool unavailable '+name);versions[name]=p.stdout.splitlines()[0]
    require('14.2.1' in versions['gcc'] and '2.44' in versions['ld'],'tool version mismatch')
    # Validate GCC's actual cc1/as resolution without creating any output file.
    for prog in ('cc1','as'):
        p=subprocess.run([str(tools['gcc']),'-B'+str(toolroot/'bin')+'/arm-none-eabi-', '-print-prog-name='+prog],env=env,text=True,capture_output=True)
        require(p.returncode==0 and Path(p.stdout.strip()).resolve(strict=True)==tools[prog], 'unexpected compiler backing program: '+prog)
    source_bytes={name:p.read_bytes() for name,p in sources.items()}
    require(hashlib.sha256(source_bytes['mt_reset_stm32f103c8.S']).hexdigest()==args.expected_reset_sha,'reset changed before output')
    require(hashlib.sha256(source_bytes['stm32f103c8.ld']).hexdigest()==args.expected_linker_sha,'linker changed before output')
    input_manifest=[dict(path=str(p),sha256=digest(p),bytes=p.stat().st_size,device=p.stat().st_dev,inode=p.stat().st_ino) for p in [own,validator,vector,*sources.values(),*tools.values()]]
    out.mkdir();(out/'inputs').mkdir()
    for name,p in sources.items():
        (out/'inputs'/name).write_bytes(source_bytes[name])
        require(digest(out/'inputs'/name)==digest(p),'input changed during snapshot')
    (out/'inputs/vector_map.json').write_bytes(vector_bytes)
    (out/'manifest.json').write_text(json.dumps(dict(argv=sys.argv,inputs=input_manifest,python=dict(version=sys.version,executable=str(Path(sys.executable).resolve()),sha256=digest(Path(sys.executable).resolve())),versions=versions,environment=env,claim='BOOT001 issue2 comment6101509227; supporting tests, no independent acceptance'),indent=2)+'\n')
    run=Run(out,tools,env,rows)
    reset=source_bytes['mt_reset_stm32f103c8.S'].decode();linker=source_bytes['stm32f103c8.ld'].decode()
    # All combinations ensure independently empty or non-word logical ranges.
    for dn,bn in itertools.product((0,1,3,4,5),repeat=2):
        e,r=run.case(f'ranges_d{dn}_b{bn}',source=cfixture(dn,bn))
        if e:
            try:r['range_model']=model_ranges(e,dn,bn)
            except AssertionError as ex:r['passed']=False;r['errors'].append(str(ex))
    base,br=run.case('baseline')
    br['vector_relocations']=check_vector_relocations(ELF(out/'baseline/reset.o'),rows)
    override,orr=run.case('irq_override',extra='.section .text.TIM2_IRQHandler,"ax",%progbits\n.global TIM2_IRQHandler\n.type TIM2_IRQHandler,%function\n.thumb_func\nTIM2_IRQHandler: bx lr\n.size TIM2_IRQHandler,.-TIM2_IRQHandler\n')
    if base and override:
        differences=[i for i,(a,b) in enumerate(zip(br.get('vectors',[]),orr.get('vectors',[]))) if a!=b]
        orr['only_changed_vector_indices']=differences
        if differences!=[44] or override.symbols['TIM2_IRQHandler']['bind']!=1:orr['passed']=False;orr['errors'].append('override must alter only TIM2 vector index44')
    run.case('missing_main',False,source=None,diag=['missing main','undefined reference to `main'])
    run.case('missing_reset',False,reset=reset.replace('Reset_Handler','Alternate_Reset'),diag=['missing Reset_Handler'])
    first=reset.index('.section .isr_vector');last=reset.index('.size mt_boot_vectors, . - mt_boot_vectors')+len('.size mt_boot_vectors, . - mt_boot_vectors')
    run.case('missing_vectors',False,reset=reset[:first]+reset[last:],diag=['expected exactly 59 vector words'])
    run.case('duplicate_vectors',False,extra='.section .isr_vector.other,"a",%progbits\n.global another_table\nanother_table: .space 236\n',diag=['expected exactly 59 vector words'])
    run.case('duplicate_reset',False,extra='.section .text.duplicate,"ax",%progbits\n.global Reset_Handler\n.type Reset_Handler,%function\n.thumb_func\nReset_Handler: bx lr\n',diag=['multiple definition'])
    run.case('vector_words58',False,reset=reset.replace('    .word USBWakeUp_IRQHandler\n',''),diag=['expected exactly 59 vector words'])
    run.case('vector_words60',False,reset=reset.replace('    .word USBWakeUp_IRQHandler\n','    .word USBWakeUp_IRQHandler\n    .word 0\n'),diag=['expected exactly 59 vector words'])
    run.case('vector_base_shift256',False,linker=linker.replace('.isr_vector ORIGIN(FLASH)', '.isr_vector (ORIGIN(FLASH) + 256)'),diag=['vector base must be Flash origin','vector base must align'])
    for name,n,di in [('absent',None,['undefined symbol','explicit __boot_stack_bytes']),('zero',0,['invalid stack reservation']),('non8',4097,['stack reservation must align']),('over_ram',20488,['invalid stack reservation'])]:
        run.case('stack_'+name,False,stack=n,diag=di)
    run.case('stack_full_ram_empty',True,stack=20480)
    run.case('stack_2048_geometry_only',True,stack=2048)
    for name,n,expect,diag in [('touch',16384,True,None),('overlap',16385,False,['static storage overlaps']),('sram_overflow',20481,False,['will not fit','outside SRAM','overflowed'])]:
        run.case('bss_'+name,expect,extra=f'.section .bss.fill,"aw",%nobits\n.global filler\nfiller: .space {n}\n',retain=['filler'],diag=diag)
    run.case('data_sram_overflow',False,extra='.section .data.fill,"aw",%progbits\n.global filler\nfiller: .space 20481,0x5a\n',retain=['filler'],diag=['will not fit','outside SRAM','overflowed'])
    # Exact final permitted Flash byte and +1 byte including word padding.
    require(base is not None,'baseline required for boundary arithmetic')
    flash_used=max(p['paddr']+p['filesz'] for p in base.segments if p['type']==1 and p['filesz'])-FLASH
    for delta in (0,1):
        run.case('flash_code_'+('touch' if delta==0 else 'overflow'),delta==0,extra=f'.section .rodata.fill,"a",%progbits\n.global filler\nfiller: .space {65536-flash_used+delta},0x5a\n',retain=['filler'],diag=None if delta==0 else ['will not fit','overflowed'])
    for n in (3,4,5):
        extra=f'.section .rodata.fill,"a",%progbits\n.global filler\nfiller: .space {65536-flash_used-4},0x5a\n.section .data.edge,"aw",%progbits\n.global edge_data\nedge_data: .space {n},0x5a\n'
        run.case(f'flash_data_lma_{n}',n<=4,extra=extra,retain=['filler','edge_data'],diag=None if n<=4 else ['data load image outside Flash','overflowed'])
    unsupported=['.preinit_array','.preinit_array.100','.init_array','.init_array.100','.fini_array','.ctors','.dtors','.init','.fini','.tdata','.tbss','.noinit']
    for section in unsupported:
        flags='awT' if section in ('.tdata','.tbss') else 'aw'
        kind='%nobits' if section in ('.tbss','.noinit') else '%progbits'
        run.case('reject_'+section[1:].replace('.','_'),False,extra=f'.section {section},"{flags}",{kind}\n.space 4\n',diag=['constructors, TLS, noinit and COMMON unsupported'])
    for section in unsupported:
        flags='awT' if section in ('.tdata','.tbss') else 'aw'
        kind='%nobits' if section in ('.tbss','.noinit') else '%progbits'
        run.case('reject_gc_'+section[1:].replace('.','_'),False,extra=f'.section {section},\"{flags}\",{kind}\n.space 4\n',gc=True,diag=['constructors, TLS, noinit and COMMON unsupported'])
    run.case('reject_COMMON',False,extra='.comm forbidden_common,4,4\n',diag=['constructors, TLS, noinit and COMMON unsupported'])
    run.case('reject_gc_COMMON',False,extra='.comm forbidden_common,4,4\n',gc=True,diag=['constructors, TLS, noinit and COMMON unsupported'])
    for section in ('.ARM.extab.fixture','.ARM.exidx.fixture'):
        run.case('reject_'+section[1:].replace('.','_'),False,extra=f'.section {section},"a",%progbits\n.word 1,1\n',diag=['unwind tables unsupported'])
    for section in ('.ARM.extab.fixture','.ARM.exidx.fixture'):
        run.case('reject_gc_'+section[1:].replace('.','_'),False,extra=f'.section {section},\"a\",%progbits\n.word 1,1\n',gc=True,diag=['unwind tables unsupported'])
    for section in ('.glue_7','.glue_7t','.vfp11_veneer','.v4_bx','.iplt','.igot.plt'):
        run.case('reject_'+section[1:].replace('.','_'),False,extra=f'.section {section},"ax",%progbits\n.word 1\n',diag=['interworking and dynamic stubs unsupported'])
    orphan='.section .unexpected_alloc,"a",%progbits\n.global unexpected_alloc\nunexpected_alloc: .word 0x12345678\n'
    run.case('orphan_retained_gc',False,extra=orphan,retain=['unexpected_alloc'],gc=True,diag=['unplaced orphan section'])
    _, counterexample=run.case('orphan_unreferenced_gc_unsupported',True,extra=orphan,gc=True,diag=["removing unused section '.unexpected_alloc'"])
    counterexample['supported_profile']=False
    counterexample['meaning']='Counterexample: arbitrary unreferenced input orphans escape --orphan-handling=error with GC. Supported profile requires --no-gc-sections.'
    run.case('orphan_unreferenced_no_gc',False,extra=orphan,gc=False,diag=['unplaced orphan section'])
    # Linker DEFINED() does not prove binding or code type; validator is mandatory.
    bad_main_sources={
        'weak':MAIN.replace('.global main','.weak main'),
        'object':ASM_HEADER+'.section .text.main,\"ax\",%progbits\n.global main\n.type main,%object\nmain: .word 0\n.size main,.-main\n'+ASM_FOOTER,
        'absolute_thumb':ASM_HEADER+'.global main\n.type main,%function\n.thumb_set main,0x08000159\n.size main,4\n'+ASM_FOOTER,
        'absolute':ASM_HEADER+'.global main\n.set main,0x08000159\n'+ASM_FOOTER,
    }
    for name,source in bad_main_sources.items():
        e,r=run.case('invalid_main_'+name,name not in ('object','absolute'),source=source,inspect=False,diag=['Unknown destination type'] if name in ('object','absolute') else None)
        if r['link_returncode']==0:
            e=ELF(out/r['name']/'image.elf')
            try:check_layout(e); rejected=False
            except AssertionError as ex:rejected=True;r['checker_diagnostic']=str(ex)
            r['expected']='link_success_then_checker_refusal'
            r['passed']=rejected
            r['meaning']='Counterexample: linker DEFINED(main) alone is insufficient; artifact validator rejects it.'
            r['elf_sha256']=digest(out/r['name']/'image.elf')
    # Metadata counterexamples: supported flow refuses input before raw ld can discard it.
    for name,section,field,value in [('alloc_comment','.comment','flags',2),('alloc_gnu_stack','.note.GNU-stack','flags',2),('alloc_arm_attributes','.ARM.attributes','flags',2),('wrong_arm_attributes_type','.ARM.attributes','type',1)]:
        folder=out/name;folder.mkdir()
        src=folder/'metadata.c';src.write_text('/* test-only metadata probe */\nint probe(void) { return 0; }\n')
        obj=folder/'metadata.o';run.compile(src,obj)
        parsed=ELF(obj)
        if section not in parsed.by_name:
            src=folder/'metadata.S';src.write_text(ASM_HEADER+'.section .note.GNU-stack,\"\",%progbits\n.word 0x12345678\n')
            run.compile(src,obj);parsed=ELF(obj)
        sec=parsed.by_name[section];raw=bytearray(parsed.raw)
        struct.pack_into('<I',raw,parsed.shoff+sec['index']*parsed.shsize+({'type':4,'flags':8}[field]),value)
        obj.write_bytes(raw)
        try:audit_input_metadata(ELF(obj));refused=False;reason=''
        except AssertionError as ex:refused=True;reason=str(ex)
        # This direct link is deliberately outside the supported audited flow.
        lp=run.command([tools['ld'],'--defsym=__boot_stack_bytes=4096','-T',out/'inputs/stm32f103c8.ld','--no-gc-sections','--orphan-handling=error','-Map='+str(folder/'raw.map'),out/'baseline/reset.o',out/'baseline/fixture.o',obj,'-o',folder/'raw.elf'],folder)
        (folder/'raw_link.stderr').write_text(lp.stderr)
        inspect_raw=run.command([tools['readelf'],'-W','-h','-S','-l',obj],folder)
        (folder/'input_readelf.txt').write_text(inspect_raw.stdout+inspect_raw.stderr)
        raw_elf_result=None
        if lp.returncode==0:
            ep=ELF(folder/'raw.elf')
            try:check_layout(ep);raw_elf_result='accepted by final ELF layout validator'
            except AssertionError as ex:raw_elf_result='rejected by final ELF layout validator: '+str(ex)
        run.results.append(dict(name='input_audit_'+name,expected='input_audit_refusal_before_supported_link',passed=refused,checker_diagnostic=reason,raw_unsupported_link_returncode=lp.returncode,raw_elf_result=raw_elf_result,input_sha256=digest(obj),section_mutation=dict(section=section,field=field,value=value)))
    # Checker mutation tests distinguish a checker refusal from linker refusal.
    for name,index,val in [('reserved_slot',7,base.value('Default_Handler')),('thumb_bit',1,base.value('Reset_Handler')&~1),('wrong_irq',44,base.value('Reset_Handler')),('initial_msp',0,RAM_END-4)]:
        path=out/(name+'_mutant.elf');raw=bytearray(base.raw)
        struct.pack_into('<I',raw,base.by_name['.isr_vector']['offset']+4*index,val);path.write_bytes(raw)
        caught=False
        try:check_vectors(ELF(path),rows)
        except AssertionError:caught=True
        run.results.append(dict(name='checker_mutation_'+name,expected='checker_refusal',passed=caught,mutation_index=index,mutant_sha256=digest(path)))
    # Mutating symbolic IRQ order is detectable even where final weak addresses coincide.
    swapped=reset.replace('    .word WWDG_IRQHandler\n    .word PVD_IRQHandler', '    .word PVD_IRQHandler\n    .word WWDG_IRQHandler')
    folder=out/'swapped_symbolic_irq_order';folder.mkdir()
    src=folder/'reset.S';src.write_text(swapped);obj=folder/'reset.o';run.compile(src,obj)
    try:check_vector_relocations(ELF(obj),rows);caught=False
    except AssertionError:caught=True
    run.results.append(dict(name='checker_mutation_symbolic_irq_swap',expected='object_relocation_checker_refusal',passed=caught,input_sha256=digest(obj)))
    for name in ('__data_start','__data_end','__bss_start','__bss_end','__static_end'):
        reference=ELF(out/'ranges_d5_b5/image.elf');raw=bytearray(reference.raw)
        symtab=next(sec for sec in reference.sections if sec['type']==2)
        symbols=reference.symbol_tables[symtab['index']]
        index=next(i for i,(symbol,_) in enumerate(symbols) if symbol==name)
        struct.pack_into('<I',raw,symtab['offset']+index*16+4,reference.value(name)+4)
        path=out/(name+'_shifted.elf');path.write_bytes(raw)
        try:check_layout(ELF(path));caught=False;reason=''
        except AssertionError as ex:caught=True;reason=str(ex)
        run.results.append(dict(name='checker_mutation_'+name,expected='section_extent_checker_refusal',passed=caught,checker_diagnostic=reason,elf_sha256=digest(path)))
    # ABI/header refusal probes do not represent target execution.
    for name,offset,fmt,value in [('wrong_class',4,'B',2),('wrong_endian',5,'B',2),('wrong_machine',18,'H',62),('hard_float_flag',36,'I',ELF(out/'baseline/reset.o').flags|0x400)]:
        path=out/(name+'_object.o');raw=bytearray((out/'baseline/reset.o').read_bytes())
        struct.pack_into('<'+fmt,raw,offset,value);path.write_bytes(raw)
        try:audit_input_metadata(ELF(path));caught=False;reason=''
        except AssertionError as ex:caught=True;reason=str(ex)
        run.results.append(dict(name='input_abi_'+name,expected='input_audit_refusal',passed=caught,checker_diagnostic=reason,input_sha256=digest(path)))
    for name,flags in [('cortex_m4_dsp',['-mcpu=cortex-m4']),('cortex_a8',['-mcpu=cortex-a8']),('short_enums',['-mcpu=cortex-m3','-fshort-enums']),('vfp_args',['-mcpu=cortex-m3','-mfpu=vfpv3-d16','-mfloat-abi=hard'])]:
        folder=out/('input_abi_'+name);folder.mkdir()
        src=folder/'probe.c';src.write_text('enum probe { A, B }; enum probe item; int main(void) { return (int)item; }\n')
        obj=folder/'probe.o'
        p=run.command([tools['gcc'],'-B'+str(toolroot/'bin')+'/arm-none-eabi-','-mthumb','-ffreestanding','-fno-common','-g0','-Os',*flags,'-c',src,'-o',obj],folder)
        require(p.returncode==0,'ABI negative probe compilation failed: '+p.stderr)
        attrs=run.command([tools['readelf'],'-A',obj],folder)
        (folder/'attributes.txt').write_text(attrs.stdout+attrs.stderr)
        try:check_attributes(attrs.stdout);caught=False;reason=''
        except AssertionError as ex:caught=True;reason=str(ex)
        run.results.append(dict(name='input_abi_'+name,expected='attribute_checker_refusal',passed=caught,checker_diagnostic=reason,input_sha256=digest(obj),attribute_text=attrs.stdout))
    # Full deterministic rerun in another fresh case directory.
    repeat,rr=run.case('baseline_clean_repeat')
    rr['same_elf_sha256_as_baseline']=rr.get('elf_sha256')==br.get('elf_sha256')
    if not rr['same_elf_sha256_as_baseline']:rr['passed']=False;rr['errors'].append('baseline repeat differs')
    (out/'results.json').write_text(json.dumps(run.results,indent=2)+'\n')
    failures=[r['name'] for r in run.results if not r['passed']]
    summary=dict(total=len(run.results),passed=len(run.results)-len(failures),failed=len(failures),failures=failures,verdict='TEST_FAILURES' if failures else 'SUPPORTING_CHECKS_PASS',acceptance='BLOCKED_EVIDENCE: independent review and immutable commit binding not supplied',limitations=['No ARM ISA/MCU execution','No physical board, flash, USB, clock, time hook, full component closure or product stack bound','Native range model confirms only modeled interval arithmetic','No safety/shutdown claim'],flash_baseline_bytes=flash_used)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    hashes=[dict(path=str(p.relative_to(out)),sha256=digest(p),bytes=p.stat().st_size) for p in sorted(out.rglob('*')) if p.is_file()]
    (out/'artifact_manifest.json').write_text(json.dumps(hashes,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    return 1 if failures else 0

if __name__=='__main__':
    try:sys.exit(main())
    except (AssertionError,FileNotFoundError,ValueError,KeyError) as error:
        print('ERROR: '+str(error),file=sys.stderr);sys.exit(2)
