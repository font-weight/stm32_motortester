#!/usr/bin/env python3
"""Extract original DCD bodies; instrument only model ISTR access semantics."""
import json,re
from pathlib import Path
from extract_base import function,slice_unique,sha,DRIVER_FUNCTIONS,HEADER_FUNCTIONS,COMMON_FUNCTIONS

def generate(driver,header,common,out):
    manifest={'source_sha256':{'driver':sha(driver),'header':sha(header),'common':sha(common)},'functions':[],'access_shims':[]}
    constants=slice_unique(header,'#define U_EP_CTR_RX','// Registers Typedef')+'#define FSDEV_EP_COUNT 8\n'
    constants+=slice_unique(header,'typedef enum {\n  EP_STAT_DISABLED','TU_ATTR_ALWAYS_INLINE static inline uint32_t ep_read')
    (out/'constants.inc').write_text(constants)
    types=slice_unique(driver,'typedef struct {\n  uint8_t   *buffer;','#if defined(TUP_USBIP_FSDEV_CH32)\nstatic bool       ep0_ctrl_dir_in;')
    if 'bool mt_usb_fsdev_ctr_rearm' in types:
        for name in ['mt_usb_fsdev_ctr_deferred','mt_usb_fsdev_ctr_rearm']:
            raw,_,_=function(driver,name); emitted=raw
            if name.endswith('_rearm'):
                assert emitted.count('FSDEV_REG->ISTR')==1
                emitted=emitted.replace('FSDEV_REG->ISTR','model_istr_read()')
                manifest['access_shims'].append({'function':name,'reads':1,'stores':0})
            manifest['functions'].append({'name':name,'raw_sha256':sha(raw),'emitted_sha256':sha(emitted),'byte_identical':raw==emitted})
            types=types.replace(raw,emitted)
    (out/'state.inc').write_text(types)
    manifest['state_sha256']=sha(types)
    def funcs(source,names):
        acc=[]
        for name in names:
            if name=='dcd_int_handler':
                raw=slice_unique(source,'void dcd_int_handler(uint8_t rhport) {','//--------------------------------------------------------------------+\n// Endpoint API').rstrip()
                assert raw.endswith('\n}')
            else: raw,_,_=function(source,name)
            emitted=raw
            if name=='dcd_int_handler':
                emitted,n=re.subn(r'FSDEV_REG->ISTR = ([^;\n]+);',r'model_istr_write(\1);',emitted)
                assert n==8, n
                reads=emitted.count('FSDEV_REG->ISTR')
                assert reads==(4 if 'mt_ctr_budget' in raw else 3), reads
                emitted=emitted.replace('FSDEV_REG->ISTR','model_istr_read()')
                manifest['access_shims'].append({'function':name,'reads':reads,'stores':n})
            manifest['functions'].append({'name':name,'raw_sha256':sha(raw),'emitted_sha256':sha(emitted),'byte_identical':raw==emitted})
            acc.append(emitted)
        return '\n\n'.join(acc)+'\n'
    (out/'common.inc').write_text(funcs(header,HEADER_FUNCTIONS)+funcs(common,COMMON_FUNCTIONS))
    (out/'driver.inc').write_text(funcs(driver,DRIVER_FUNCTIONS+['dcd_edpt_iso_alloc','dcd_edpt_iso_activate']))
    (out/'reset.inc').write_text(funcs(driver,['handle_bus_reset']))
    (out/'extraction-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
