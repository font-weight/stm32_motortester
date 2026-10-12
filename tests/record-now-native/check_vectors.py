#!/usr/bin/env python3
"""Replay independently reviewed PR141 bytes through the actual draft0.2 codec."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def main():
    catalogue = json.loads(Path(__file__).with_name('vectors.json').read_text())
    chunks = ['#include "firmware/protocol/mt_command.h"\n#include <assert.h>\n#include <string.h>\nint main(void) {']
    for v in catalogue['vectors']:
        frame = bytes.fromhex(v['frame_hex']); f = v['fields']
        result = 'MT_WIRE_CRC' if v['name'] == 'bad_crc_before_version' else ('MT_WIRE_OK' if f['minor'] == 2 else 'MT_WIRE_VERSION')
        chunks.append('{ const unsigned char frame[]={' + ','.join(map(str, frame)) + '};')
        chunks.append('mt_wire_message m, saved; memset(&m, 0xa5, sizeof m); memcpy(&saved, &m, sizeof m);')
        chunks.append(f'assert(mt_wire_decode(frame,sizeof frame-1,&m)=={result});')
        if result != 'MT_WIRE_OK':
            chunks.append('assert(!memcmp(&m,&saved,sizeof m)); }')
            continue
        chunks.append(f'assert(m.type=={f["type"]}u && m.session==UINT64_C({f["session"]}) && m.sequence=={f["sequence"]}u && m.request=={f["request"]}u);')
        payload = bytes.fromhex(f['payload_hex'])
        chunks.append(f'assert(m.payload_length=={len(payload)}u);')
        if payload:
            chunks.append('const unsigned char payload[]={' + ','.join(map(str,payload)) + '}; assert(!memcmp(m.payload,payload,sizeof payload));')
        chunks.append('unsigned char encoded[MT_WIRE_FRAME_MAX]; size_t n=0; assert(mt_wire_encode(&m,encoded,sizeof encoded,&n)==MT_WIRE_OK); assert(n==sizeof frame && !memcmp(encoded,frame,n));')
        command = 'MT_COMMAND_WRONG_DIRECTION' if f['type'] >= 128 else ('MT_COMMAND_LENGTH' if f['type']==8 and len(payload)!=4 else 'MT_COMMAND_OK')
        chunks.append('mt_command c, old; memset(&c,0xa5,sizeof c); memcpy(&old,&c,sizeof c);')
        chunks.append(f'assert(mt_command_parse(&m,&c)=={command});')
        if command != 'MT_COMMAND_OK':
            chunks.append('assert(!memcmp(&c,&old,sizeof c));')
        chunks.append('}')
    chunks.append('return 0; }')
    with tempfile.TemporaryDirectory(prefix='record-now-vectors-') as directory:
        d=Path(directory); source=d/'vectors.c'; source.write_text('\n'.join(chunks)+'\n')
        for mode, flags in [('strict', ['-O2']), ('ubsan', ['-O1','-fsanitize=undefined','-fno-sanitize-recover=all'])]:
            argv=[os.environ.get('CC','cc'), '-std=c11','-Wall','-Wextra','-Wpedantic','-Werror',*flags,'-I',str(ROOT),str(source),str(ROOT/'firmware/protocol/mt_wire.c'),str(ROOT/'firmware/protocol/mt_command.c'),'-o',str(d/mode)]
            subprocess.run(argv,check=True,timeout=120)
            subprocess.run([d/mode],check=True,timeout=60)
            print(f'{mode}: {len(catalogue["vectors"])} fixed wire vectors passed',flush=True)

if __name__ == '__main__':
    main()
