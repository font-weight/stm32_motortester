#!/usr/bin/env python3
"""Fail-closed extraction of pinned C functions, not a general C parser."""
import hashlib
import re

def sha(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()

def function(text, name):
    # Match definitions only; prototypes and call expressions cannot contain a body.
    pattern = re.compile(r'(?m)^[A-Za-z_][A-Za-z_0-9 *\t]*\b' + re.escape(name) + r'\s*\([^;{}]*\)\s*\{')
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f'{name}: expected one definition, found {len(matches)}')
    start = matches[0].start()
    pos = matches[0].end() - 1
    depth = 0
    state = 'code'
    i = pos
    while i < len(text):
        ch = text[i]
        nx = text[i:i+2]
        if state == 'code':
            if nx == '//': state = 'line'; i += 2; continue
            if nx == '/*': state = 'comment'; i += 2; continue
            if ch in '\"\'': state = ch
            elif ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i+1], start, i+1
        elif state == 'line':
            if ch == '\n': state = 'code'
        elif state == 'comment':
            if nx == '*/': state = 'code'; i += 2; continue
        elif ch == '\\': i += 2; continue
        elif ch == state: state = 'code'
        i += 1
    raise ValueError(f'{name}: unterminated body')

def slice_unique(text, begin, end):
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError('non-unique section delimiter')
    a = text.index(begin); b = text.index(end, a)
    return text[a:b]

DRIVER_FUNCTIONS = ['edpt0_prepare_setup','xfer_ctl_ptr','dcd_pma_alloc','dcd_ep_alloc',
    'edpt0_open','dcd_edpt_open','dcd_transmit_packet','edpt_xfer','dcd_edpt_xfer',
    'dcd_edpt_xfer_fifo','handle_ctr_tx','handle_ctr_setup','handle_ctr_rx','dcd_int_handler']
HEADER_FUNCTIONS = ['ep_read','ep_write_clear_ctr','ep_change_status','ep_change_dtog','ep_is_iso',
    'btable_get_addr','btable_set_addr','btable_get_count','btable_set_count']
COMMON_FUNCTIONS = ['pma_align_buffer_size','btable_set_rx_bufsize']

def generate(driver, header, common, out):
    manifest = {'source_sha256': {'driver':sha(driver),'common_h':sha(header),'common_c':sha(common)},
                'functions': [], 'sections': [], 'access_shims': []}
    def section(name, source, begin, end):
        chunk = slice_unique(source, begin, end)
        manifest['sections'].append({'name':name,'sha256':sha(chunk)})
        return chunk
    constants = section('register bit definitions',header,'#define U_EP_CTR_RX', '// Registers Typedef')
    physical_count = re.findall(r'(?m)^#define FSDEV_EP_COUNT 8$', header)
    if len(physical_count) != 1: raise ValueError('unexpected physical endpoint count definition')
    constants += physical_count[0] + '\n'
    constants += section('endpoint status enum and masks',header,'typedef enum {\n  EP_STAT_DISABLED', 'TU_ATTR_ALWAYS_INLINE static inline uint32_t ep_read')
    types = section('transfer and allocation declarations',driver,'typedef struct {\n  uint8_t   *buffer;', '#if defined(TUP_USBIP_FSDEV_CH32)\nstatic bool       ep0_ctrl_dir_in;')
    (out/'constants.inc').write_text(constants)
    (out/'state.inc').write_text(types)
    def funcs(source, names, source_name):
        acc=[]
        for name in names:
            raw,a,b=function(source,name)
            emitted=raw
            if name=='dcd_int_handler':
                emitted,n=re.subn(r'FSDEV_REG->ISTR = ([^;\n]+);',r'model_istr_write(\1);',emitted)
                if n!=6:
                    raise ValueError(f'ISR access shim expects six ISTR stores, found {n}')
                nr=emitted.count('FSDEV_REG->ISTR')
                if nr!=3:
                    raise ValueError(f'ISR access shim expects three ISTR reads, found {nr}')
                emitted=emitted.replace('FSDEV_REG->ISTR','model_istr_read()')
                manifest['access_shims'].append({'function':name,'stores':n,'reads':nr,
                    'reason':'Model ISTR rc_w0 flags and derived read-only CTR/EP_ID; no other body rewrite.'})
            manifest['functions'].append({'name':name,'source':source_name,
                'first_line':source[:a].count('\n')+1,'last_line':source[:b].count('\n')+1,
                'raw_sha256':sha(raw),'emitted_sha256':sha(emitted),'byte_identical':raw==emitted})
            acc.append(emitted)
        return '\n\n'.join(acc)+'\n'
    (out/'common.inc').write_text(funcs(header,HEADER_FUNCTIONS,'fsdev_common.h')+'\n'+funcs(common,COMMON_FUNCTIONS,'fsdev_common.c'))
    (out/'driver.inc').write_text(funcs(driver,DRIVER_FUNCTIONS,'dcd_stm32_fsdev.c'))
    (out/'reset.inc').write_text(funcs(driver,['handle_bus_reset'],'dcd_stm32_fsdev.c'))
    return manifest

def validate_candidate(original,candidate):
    _,a,b=function(original,'handle_bus_reset')
    _,c,d=function(candidate,'handle_bus_reset')
    if original[:a]!=candidate[:c] or original[b:]!=candidate[d:]:
        raise ValueError('candidate changes source outside handle_bus_reset; extraction scope must be reviewed')

def self_test(original,candidate):
    raw,_,_=function(original,'handle_bus_reset')
    for bad in [original+'\n'+raw, original.replace(raw,''), original.replace(raw,raw[:-1])]:
        try: function(bad,'handle_bus_reset')
        except ValueError: pass
        else: raise AssertionError('malformed extraction accepted')
    for bad in [candidate.replace('static void handle_ctr_tx','static void altered_handle_ctr_tx'), candidate+'\n/* extra */']:
        try: validate_candidate(original,bad)
        except ValueError: pass
        else: raise AssertionError('out-of-scope candidate accepted')
    synthetic='static int f(void) { const char *s = "}\\\"{"; /* } */ // {\n return 1; }'
    assert function(synthetic,'f')[0]==synthetic
    return {'duplicate_missing_unterminated_rejected':True,'out_of_scope_changes_rejected':True,
            'comment_string_braces_handled':True}
