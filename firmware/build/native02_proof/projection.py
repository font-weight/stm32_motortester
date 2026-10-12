"""Explore direct-edge path projection. Not callback or whole-stack proof."""
from pathlib import Path
import importlib.util, sys, json
sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[3]
s = importlib.util.spec_from_file_location('reader', root / 'tests/boot-link/tools/run_boot_link_tests.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

def graph(directory):
    ref = json.loads((root / 'firmware/build/stack_reference.json').read_text())
    objects = {x['name']: m.ELF(directory / x['name']) for x in ref['original_objects_in_order']}
    defs = {}
    nodes = set()
    edges = set()
    for obj, e in objects.items():
        for name, sym in sum(e.symbol_tables.values(), []):
            if sym['ndx'] not in (0, 65521) and sym['type'] == 2:
                key = obj + '::' + e.sections[sym['ndx']]['name']
                nodes.add(key)
                if sym['bind'] in (1, 2):
                    defs.setdefault(name, []).append((sym['bind'], key))
    for obj, e in objects.items():
        for rs in e.sections:
            if rs['type'] != 9:
                continue
            sec = e.sections[rs['info']]
            if not sec['flags'] & 4:
                continue
            for off in range(rs['offset'], rs['offset'] + rs['size'], 8):
                place, info = e.unpack('<II', off)
                typ = info & 255
                if typ not in (10, 30):
                    continue
                name, sym = e.symbol_tables[rs['link']][info >> 8]
                expected = bytes.fromhex('fff7feff' if typ == 10 else 'fff7febf')
                if e.bytes(sec)[place:place + 4] != expected:
                    raise ValueError('non-entry direct relocation addend')
                if (sym['type'], sym['value'], sym['ndx'] == 0) not in {(2, 1, False), (0, 0, True)}:
                    raise ValueError('non-function direct target')
                if sym['ndx'] and sym['bind'] == 0:
                    target = obj + '::' + e.sections[sym['ndx']]['name']
                else:
                    candidates = defs[name]
                    strong = [n for b, n in candidates if b == 1]
                    if len(strong) == 1:
                        target = strong[0]
                    elif len(candidates) == 1:
                        target = candidates[0][1]
                    else:
                        raise ValueError(('ambiguous', name, candidates))
                source = obj + '::' + sec['name']
                if source not in nodes or target not in nodes:
                    raise ValueError(('missing node', source, target))
                edges.add((source, target))
    return (nodes, edges)

def project(oldnodes, old, newnodes, new):
    projection = {'3-mt_control.o::.text.start_capture': '3-mt_control.o::.text.mt_control_receive', '1-mt_capture_link.o::.text.mt_capture_link_record_now': '1-mt_capture_link.o::.text.mt_capture_link_arm', '0-acquisition.o::.text.mt_acq_record_now': '0-acquisition.o::.text.mt_acq_arm'}
    f = lambda n: projection.get(n, n)
    if any((f(n) not in oldnodes for n in newnodes)):
        raise ValueError('unmapped node')
    collapsed = set()
    for a, b in new:
        if f(a) == f(b):
            collapsed.add((a, b))
        elif (f(a), f(b)) not in old:
            raise ValueError('uncovered projected edge: ' + str((a, b)))
    expected = {('3-mt_control.o::.text.mt_control_receive', '3-mt_control.o::.text.start_capture')}
    if collapsed != expected:
        raise ValueError('unexpected stuttering edge set')
    return {'projection': projection, 'collapsed_edges': sorted(collapsed), 'old_nodes': len(oldnodes), 'new_nodes': len(newnodes), 'old_direct_pairs': len(old), 'new_direct_pairs': len(new)}
