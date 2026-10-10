#!/usr/bin/env python3
"""Critic-A negative ARM builds using private copies of the real TinyUSB config."""
import hashlib,json,os,pathlib,subprocess
root=pathlib.Path(__file__).resolve().parent
up=pathlib.Path(os.environ.get('USB_DEPS','/workspace/shared/usb-dependencies'))
tc=pathlib.Path(os.environ.get('TC','/workspace/shared/arm-resource-evidence/toolchain/root/usr'))
original=up/'evidence/config/tusb_config.h';before=original.read_bytes()
work=root/'guard-build';work.mkdir(exist_ok=True)
base=[str(tc/'bin/arm-none-eabi-gcc'),'-std=c11','-Os','-mcpu=cortex-m3','-mthumb','-ffreestanding','-fno-short-enums','-Wall','-Wextra','-Wpedantic','-Wconversion','-Wsign-conversion','-Werror','-DSTM32F103xB','-isystem',str(tc/'include/newlib'),'-isystem',str(up/'vendor/tinyusb/src')]
cases=[('valid',None,None),('cdc0','CFG_TUD_CDC',0),('cdc2','CFG_TUD_CDC',2),('overwrite','CFG_TUD_CDC_TX_OVERWRITABLE_IF_NOT_CONNECTED',1),('rx_persistent','CFG_TUD_CDC_RX_PERSISTENT',1),('tx_persistent','CFG_TUD_CDC_TX_PERSISTENT',1)]
results=[]
for name,macro,value in cases:
 d=work/name;d.mkdir(exist_ok=True);text=before.decode()
 if macro:
  import re
  text,n=re.subn(r'(^#define\s+'+macro+r'\s+)\S+',lambda m:m.group(1)+str(value),text,flags=re.M)
  assert n==1,(macro,n)
 (d/'tusb_config.h').write_text(text)
 cmd=base+['-I',str(d),'-c',str(root/'snapshot/firmware/transport/mt_usb_cdc.c'),'-o',str(d/'adapter.o')]
 p=subprocess.run(cmd,capture_output=True,text=True);output=p.stdout+p.stderr
 (d/'build.log').write_text('+ '+' '.join(cmd)+'\n'+output)
 if macro: assert p.returncode!=0 and 'USB adapter requires one non-overwritable, nonpersistent CDC interface' in output,name
 else: assert p.returncode==0,output
 result={'name':name,'command':cmd,'returncode':p.returncode,'config_sha256':hashlib.sha256((d/'tusb_config.h').read_bytes()).hexdigest(),'expected_guard_failure':bool(macro)}
 if not macro: result['object_sha256']=hashlib.sha256((d/'adapter.o').read_bytes()).hexdigest()
 results.append(result)
 assert original.read_bytes()==before,'Original config was changed'
print('PASS: valid real-config ARM compile and five deliberate configuration rejections; original config unchanged.')
(work/'results.json').write_text(json.dumps({'source_sha256':hashlib.sha256((root/'snapshot/firmware/transport/mt_usb_cdc.c').read_bytes()).hexdigest(),'original_config_sha256':hashlib.sha256(before).hexdigest(),'results':results},indent=2)+'\n')
