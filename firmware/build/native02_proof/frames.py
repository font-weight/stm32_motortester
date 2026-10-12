# Any IT-controlled transfer or SP mutation requires a separate proof, not optimistic fallthrough.
"""Root's bounded object-local CFG/SP audit; no whole-image transfer verdict."""
from pathlib import Path
import importlib.util, json, re, struct, sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
HOME = None
OBJECTS = None
s = importlib.util.spec_from_file_location('reader', ROOT / 'tests/boot-link/tools/run_boot_link_tests.py')
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)

def need(ok, message):
    if not ok:
        raise ValueError(message)
WANTED = {'0-acquisition.o': ['mt_acq_record_now', 'mt_acq_push'], '1-mt_capture_link.o': ['mt_capture_link_record_now'], '3-mt_control.o': ['start_capture', 'mt_control_receive'], '9-mt_wire.o': ['mt_wire_encode', 'mt_wire_decode'], '12-mt_command.o': ['mt_command_parse']}
BRANCH = {'b', 'beq', 'bne', 'bhi', 'bls', 'bcs', 'bcc', 'bge', 'bgt', 'ble', 'blt', 'bmi', 'bpl', 'bvs', 'bvc', 'cbz', 'cbnz'}
ORDINARY = {'adc', 'add', 'adds', 'addw', 'cmp', 'cmpeq', 'eor', 'it', 'ite', 'ittt', 'ldr', 'ldrb', 'ldrd', 'ldrh', 'lsl', 'lsls', 'lsr', 'mov', 'moveq', 'movne', 'movs', 'movw', 'mvn', 'nop', 'orr', 'orrs', 'rsb', 'rsbs', 'stmia', 'str', 'strb', 'strd', 'streq', 'strh', 'sub', 'subs', 'ubfx', 'uxtb', 'uxth', 'ldmia'}
TABLES = {'mt_control_receive': (368, 7, 2, 'r3'), 'mt_command_parse': (90, 8, 1, 'r3')}

def inspect(obj, name):
    e = m.ELF(OBJECTS / obj)
    sec = e.by_name['.text.' + name]
    raw = e.bytes(sec)
    ins = {}
    rel = {}
    for rs in e.sections:
        if rs['type'] == 9 and rs['info'] == sec['index']:
            for off in range(rs['offset'], rs['offset'] + rs['size'], 8):
                pc, info = e.unpack('<II', off)
                rel[pc] = (info & 255, e.symbol_tables[rs['link']][info >> 8][0])
    for line in (HOME / (name + '.dis')).read_text().splitlines():
        z = re.match('^\\s*([0-9a-f]+):\\s+([0-9a-f]{4}(?: [0-9a-f]{4})?)\\s+([a-z][a-z0-9.]*)\\s*(.*)$', line)
        if not z:
            continue
        pc, encoded, op, args = z.groups()
        pc = int(pc, 16)
        bs = b''.join((int(h, 16).to_bytes(2, 'little') for h in encoded.split()))
        need(raw[pc:pc + len(bs)] == bs, 'decoder bytes mismatch')
        need(pc not in ins, 'duplicate instruction')
        ins[pc] = (len(bs), op.split('.')[0], args.split('@')[0].strip())
    mapping = sorted(((sym['value'], n.split('.')[0]) for n, sym in sum(e.symbol_tables.values(), []) if sym['ndx'] == sec['index'] and n.split('.')[0] in {'$t', '$d', '$a'}))
    need(mapping and mapping[0][0] == 0 and (len({a for a, k in mapping}) == len(mapping)), 'mapping-symbol coverage')
    code = set()
    for i, (start, kind) in enumerate(mapping):
        end = mapping[i + 1][0] if i + 1 < len(mapping) else len(raw)
        need(kind in {'$t', '$d'} and 0 <= start < end <= len(raw), 'unsupported mapping interval')
        if kind == '$t':
            code.update(range(start, end))
    decoded = set()
    for pc, (size, op, args) in ins.items():
        span = set(range(pc, pc + size))
        need(not decoded.intersection(span), 'overlapping instructions')
        decoded.update(span)
    need(decoded == code, 'undecoded code or decoded data')
    states = {}
    successors = {}
    pending = [(0, 0)]
    calls = []
    exits = []
    peak = 0
    while pending:
        pc, depth = pending.pop()
        if pc in states:
            need(states[pc] == depth, 'SP merge/cycle mismatch')
            continue
        need(pc in ins, 'branch into data/undecoded byte')
        states[pc] = depth
        size, op, args = ins[pc]
        # Memory writeback through SP changes its value independently of the
        # destination register. No such form is admitted by this finite model.
        need(re.search(r'\[sp(?:,[^\]]*)?\](?:!|\s*,)', args) is None,
             'unmodeled SP memory writeback')
        after = depth
        succ = [pc + size]
        regs = re.search('\\{([^}]+)\\}', args)
        if op in {'push', 'pop'} or (op in {'stmdb', 'ldmia'} and args.startswith('sp!')):
            need(regs is not None, 'missing register list')
            rr = [r.strip() for r in regs[1].split(',')]
            need(len(set(rr)) == len(rr) and all((re.fullmatch('r\\d+|sl|fp|ip|lr|pc', r) for r in rr)), 'unsupported register list')
            after += 4 * len(rr) * (1 if op in {'push', 'stmdb'} else -1)
        elif op in {'add', 'addw', 'sub', 'subw'} and args.startswith('sp,'):
            z = re.fullmatch('sp, (?:sp, )?#(\\d+)', args)
            need(z is not None, 'nonconstant SP update')
            after += int(z[1]) * (1 if op.startswith('sub') else -1)
        else:
            need(not re.match('(sp|pc)(?:,|!)', args), 'unhandled SP/PC destination')
            need(not (regs and re.search('\\b(sp|pc)\\b', regs[1])), 'unhandled SP/PC register load')
            if op == 'ldrd':
                need(not re.match('[^,]+, (sp|pc),', args), 'unhandled second destination')
        need(after >= 0, 'stack above entry')
        peak = max(peak, depth, after)
        if op in {'bl', 'blx'}:
            need(depth % 8 == 0, 'unaligned call')
            if op == 'bl':
                need(pc in rel and rel[pc][0] == 10, 'unbound direct call')
            else:
                need(re.fullmatch('r\\d+|sl|fp|ip', args) is not None, 'unsupported indirect call')
            calls.append({'pc': pc, 'depth': depth, 'kind': op, 'target': rel[pc][1] if op == 'bl' else args})
        elif op in BRANCH:
            if pc in rel:
                need(op == 'b' and rel[pc][0] == 30 and (after == 0), 'unbalanced or conditional tail')
                succ = []
                exits.append({'pc': pc, 'kind': 'tail', 'depth': after, 'target': rel[pc][1]})
            else:
                z = re.search('([0-9a-f]+) <', args)
                need(z is not None, 'missing local branch target')
                target = int(z[1], 16)
                succ = [target] if op == 'b' else [pc + size, target]
        elif op in {'tbb', 'tbh'}:
            need(name in TABLES, 'unknown computed jump')
            where, count, width, index = TABLES[name]
            need(pc == where and width == (1 if op == 'tbb' else 2), 'computed jump identity')
            need(ins[pc - 4][1:] == ('cmp', index + ', #' + str(count - 1)) and ins[pc - 2][1] == 'bhi', 'missing unsigned table guard')
            need(args == ('[pc, ' + index + ']' if width == 1 else '[pc, ' + index + ', lsl #1]'), 'table register mismatch')
            start = pc + 4
            need(start + count * width <= len(raw), 'table outside section')
            succ = [start + 2 * int.from_bytes(raw[start + i * width:start + (i + 1) * width], 'little') for i in range(count)]
        elif op == 'bx':
            need(args == 'lr' and after == 0, 'unbalanced/unknown branch exchange')
            succ = []
            exits.append({'pc': pc, 'kind': 'return', 'depth': after})
        elif op in {'pop', 'ldmia'} and regs and ('pc' in regs[1].split(', ')):
            need(after == 0, 'unbalanced return')
            succ = []
            exits.append({'pc': pc, 'kind': 'return', 'depth': after})
        else:
            need(op in ORDINARY or op in {'push', 'pop', 'stmdb'}, 'unclassified instruction ' + op)
        successors[pc] = succ
        pending.extend(((q, after) for q in succ))
    if name in TABLES:
        pc = TABLES[name][0]
        pred = lambda q: {p for p, targets in successors.items() if q in targets}
        need(pred(pc) == {pc - 2} and pred(pc - 2) == {pc - 4}, 'table guard bypass')
        # An out-of-range branch must leave this table path. Immediate
        # predecessor shape alone does not establish the range restriction.
        match = re.search(r'([0-9a-f]+) <', ins[pc - 2][2])
        need(match is not None, 'missing table rejection target')
        pending_reject = [int(match[1], 16)]
        visited_reject = set()
        while pending_reject:
            target = pending_reject.pop()
            need(target != pc, 'out-of-range branch can reach table')
            if target in visited_reject:
                continue
            visited_reject.add(target)
            need(target in successors, 'unclassified rejection path')
            pending_reject.extend(successors[target])

    ordered = sorted(ins)
    remaining = 0
    for pc in ordered:
        _, op, args = ins[pc]
        if remaining:
            need(op not in BRANCH | {'bl', 'blx', 'bx', 'tbb', 'tbh', 'push', 'pop', 'stmdb'} and (not args.startswith(('sp,', 'sp!', 'pc,'))), 'conditional control/SP effect')
            remaining -= 1
        if op in {'it', 'ite', 'ittt'}:
            remaining = len(op) - 1
    frames = []
    for line in (OBJECTS / (obj[:-2] + '.su')).read_text().splitlines():
        location, n, kind = line.split('\t')
        if location.rsplit(':', 1)[-1] == name:
            frames.append((int(n), kind))
    need(frames == [(peak, 'static')], 'static frame mismatch')
    unseen = [{'pc': pc, 'instruction': ins[pc]} for pc in sorted(set(ins) - set(states))]
    need(all((ins[x['pc']][1] == 'nop' for x in unseen)), 'unaccounted executable instruction')
    return {'object': obj, 'function': name, 'frame': peak, 'reachable_instructions': len(states), 'calls': calls, 'exits': exits, 'unreachable_padding': unseen}
