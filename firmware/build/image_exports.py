"""Pure byte/address verification for the current ELF-to-BIN/HEX profile.

Call only after complete static-link checks. This module grants no flash approval.
"""
import hashlib
import re
import struct

FLASH_BASE = 0x08000000
FLASH_END = 0x08010000

def require(ok, message):
    if not ok:
        raise ValueError(message)

def flash_map(elf):
    """Extract only validated file-backed Flash loads, using LMA rather than VMA."""
    result = {}
    wanted = {'.isr_vector','.text','.data'}
    seen = set()
    for section in elf.sections:
        if not section['flags'] & 2 or section['size'] == 0:
            continue
        if section['type'] == 8:
            require(section['name'] == '.bss','unexpected allocating NOBITS section')
            continue
        require(section['name'] in wanted and section['name'] not in seen and section['type']==1,
                'unexpected/duplicate Flash-loaded section')
        seen.add(section['name'])
        matches = [p for p in elf.segments if p['type']==1 and p['vaddr'] <= section['addr'] and
                   section['addr']+section['size'] <= p['vaddr']+p['filesz'] and
                   p['offset']+section['addr']-p['vaddr']==section['offset']]
        require(len(matches)==1,'ambiguous section load mapping')
        p = matches[0]
        address = p['paddr']+section['addr']-p['vaddr']
        require(FLASH_BASE <= address < address+section['size'] <= FLASH_END,'load outside STM32C8 Flash')
        content = elf.bytes(section)
        for i,value in enumerate(content):
            require(address+i not in result,'overlapping loaded Flash bytes')
            result[address+i] = value
    require(seen==wanted and result and min(result)==FLASH_BASE,'incomplete Flash image')
    return result

def decode_hex(raw, entry):
    """Decode bounded GNU Intel HEX (00/01/04/05); forbid overlaps and wrapping."""
    require(type(raw) is bytes and 0 < len(raw) <= 262144,'HEX size out of bounds')
    text = raw.replace(b'\r\n',b'\n')
    require(b'\r' not in text,'invalid HEX line ending')
    lines = text.split(b'\n')
    if lines[-1] == b'': lines.pop()
    image, upper, eof, start = {}, None, False, False
    for line in lines:
        require(not eof and re.fullmatch(b':[0-9A-Fa-f]+',line) is not None and len(line)%2==1,
                'malformed record or data after EOF')
        require(11 <= len(line) <= 521,'HEX record length out of bounds')
        record = bytes.fromhex(line[1:].decode('ascii'))
        count = record[0]
        require(len(record)==count+5 and sum(record)%256==0,'HEX length/checksum mismatch')
        address = int.from_bytes(record[1:3],'big')
        kind = record[3]; payload=record[4:-1]
        if kind == 0:
            require(upper is not None and count > 0 and address+count <= 65536,'missing base, empty data or address wrap')
            absolute = upper+address
            require(FLASH_BASE <= absolute < absolute+count <= FLASH_END,'HEX data outside Flash')
            for i,value in enumerate(payload):
                require(absolute+i not in image,'duplicate/overlapping HEX bytes')
                image[absolute+i]=value
        elif kind == 1:
            require(count==0 and address==0,'malformed EOF')
            eof = True
        elif kind == 4:
            require(count==2 and address==0,'malformed extended linear address')
            upper = int.from_bytes(payload,'big')<<16
            require(upper==FLASH_BASE,'unsupported HEX linear base')
        elif kind == 5:
            require(count==4 and address==0 and not start and int.from_bytes(payload,'big')==entry,
                    'invalid/duplicate entry record')
            start=True
        else:
            raise ValueError('unsupported HEX record type')
    require(eof and start and image,'HEX missing EOF/entry/data')
    return image

def validate_exports(elf, binary, ihex):
    """Return evidence of byte equivalence, never image/stack/hardware acceptance."""
    expected = flash_map(elf)
    require(type(binary) is bytes and len(binary) <= 65536,'BIN size/type out of bounds')
    top = max(expected)+1
    flat = bytes(expected.get(address,255) for address in range(FLASH_BASE,top))
    require(binary==flat,'BIN byte/length/base or gap-fill mismatch')
    decoded = decode_hex(ihex,elf.entry)
    require(decoded==expected,'HEX address/byte map differs from ELF Flash loads')
    # BIN contains explicit FF in holes; HEX may omit them. Current linker has none.
    canonical = b''.join(struct.pack('<IB',address,expected[address]) for address in sorted(expected))
    return {'verdict':'FLASH_EXPORT_BYTES_PASS','base':FLASH_BASE,'end_exclusive':top,
            'elf_loaded_bytes':len(expected),'binary_bytes':len(binary),'hex_data_bytes':len(decoded),
            'binary_sha256':hashlib.sha256(binary).hexdigest(),'hex_sha256':hashlib.sha256(ihex).hexdigest(),
            'address_map_sha256':hashlib.sha256(canonical).hexdigest(),
            'hardware_verified':False,'flash_approved':False,
            'limits':'Byte/address export equivalence only; original ELF checks and physical approval remain separate.'}
