#!/usr/bin/env python3
"""Generate deterministic, editable KiCad 9 candidate schematics.
All connector pin numbers belong to THIS candidate module, never Blue Pill/ST-Link.
No PCB, assembly approval, isolation or withstand rating is implied.
"""
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
import json, csv
ROOT=Path(__file__).resolve().parents[1]
def uid(s):return str(uuid5(NAMESPACE_URL,'stm32-motortester/analog-draft-v1/'+s))
def q(s):return '"'+str(s).replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')+'"'
def eff(sz=1.27,extra=''):return f'(effects (font (size {sz} {sz})) {extra})'
def prop(name,value,x=0,y=0,hide=False,left=False):return f'(property {q(name)} {q(value)} (at {x} {y} 0) {eff(1.27,("(hide yes)" if hide else "")+(" (justify left)" if left else ""))})'
def line(points):return '(polyline (pts '+ ' '.join(f'(xy {x} {y})' for x,y in points)+') (stroke (width 0.254) (type default)) (fill (type none)))'
def pin(num,name,x,y,ang,kind='passive',length=2.54):return f'(pin {kind} line (at {x} {y} {ang}) (length {length}) (name {q(name)} {eff(.9)}) (number {q(num)} {eff(.9)}))'
def rectangle(x1,y1,x2,y2):return f'(rectangle (start {x1} {y1}) (end {x2} {y2}) (stroke (width .254) (type default)) (fill (type background)))'
LIB={}
def sym(name,ref,body,units=None,foot='',data=''):
    unitstr=''.join(f'(symbol {q(name+"_"+str(i)+"_1")} {v})' for i,v in (units or {1:body}).items())
    LIB[name]=f'(symbol "AnalogDraft:{name}" (pin_names (offset .508)) (exclude_from_sim no) (in_bom yes) (on_board yes) {prop("Reference",ref)} {prop("Value",name)} {prop("Footprint",foot,hide=True)} {prop("Datasheet",data,hide=True)} {unitstr})'

sym('R_H','R',rectangle(-2.54,1.016,2.54,-1.016)+pin(1,'',-5.08,0,0)+pin(2,'',5.08,0,180),foot='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal')
sym('R_V','R',rectangle(-1.016,2.54,1.016,-2.54)+pin(1,'',0,5.08,270)+pin(2,'',0,-5.08,90),foot='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal')
sym('C_H','C',line([(-.635,2.54),(-.635,-2.54)])+line([(.635,2.54),(.635,-2.54)])+pin(1,'',-5.08,0,0,length=4.445)+pin(2,'',5.08,0,180,length=4.445))
sym('C_V','C',line([(-2.54,.635),(2.54,.635)])+line([(-2.54,-.635),(2.54,-.635)])+pin(1,'',0,5.08,270,length=4.445)+pin(2,'',0,-5.08,90,length=4.445))
sym('BAS70_V','D',line([(-1.905,-1.27),(1.905,-1.27),(0,1.27),(-1.905,-1.27)])+line([(-1.905,1.905),(-1.905,1.27),(1.905,1.27),(1.905,.635)])+pin(3,'K',0,5.08,270,length=3.81)+pin(1,'A',0,-5.08,90,length=3.81),foot='Package_TO_SOT_SMD:SOT-23',data='https://assets.nexperia.com/documents/data-sheet/BAS70.pdf')
for n in [2,3,4]:
    ys=[((n-1)/2-i)*2.54 for i in range(n)]
    sym('Conn'+str(n),'J',rectangle(0,ys[0]+2.54,5.08,ys[-1]-2.54)+''.join(pin(i+1,'',-5.08,y,0,length=5.08) for i,y in enumerate(ys)))
sym('PWR_FLAG','#FLG',line([(0,0),(0,2.54),(-1.27,1.27),(1.27,1.27),(0,2.54)])+pin(1,'pwr',0,0,90,'power_out',0))
units={}
for u,out,p,m in [(1,1,3,2),(2,7,5,6)]:
    units[u]=line([(-5.08,5.08),(-5.08,-5.08),(5.08,0),(-5.08,5.08)])+pin(p,'+',-10.16,2.54,0,'input',5.08)+pin(m,'-',-10.16,-2.54,0,'input',5.08)+pin(out,'OUT',10.16,0,180,'output',5.08)
units[3]=rectangle(-2.54,5.08,2.54,-5.08)+pin(8,'V+',0,10.16,270,'power_in',5.08)+pin(4,'V-',0,-10.16,90,'power_in',5.08)
sym('TLV9062_D','U','',units,foot='Package_SO:SOIC-8_3.9x4.9mm_P1.27mm',data='https://www.ti.com/lit/ds/symlink/tlv9062.pdf')

class Sch:
 def __init__(self,name,title):self.name=name;self.title=title;self.items=[];self.parts=[];self.used=set();self.count=0;self.uuid=uid(name)
 def ident(self,s):self.count+=1;return uid(self.name+'/'+str(self.count)+'/'+s)
 def text(self,s,x,y,size=1.27):self.items.append(f'(text {q(s)} (at {x} {y} 0) {eff(size,"(justify left top)")} (uuid {q(self.ident(s))}))')
 def wire(self,x1,y1,x2,y2):self.items.append(f'(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (stroke (width 0) (type default)) (uuid {q(self.ident("wire"))}))')
 def label(self,name,x,y):self.items.append(f'(label {q(name)} (at {x} {y} 0) {eff(1.1,"(justify left bottom)")} (uuid {q(self.ident(name))}))')
 def junction(self,x,y):self.items.append(f'(junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid {q(self.ident("junction"))}))')
 def component(self,lib,ref,value,x,y,unit=1,foot='',data='',note=''):
  self.used.add(lib);identifier=self.ident(ref+str(unit))
  if lib.startswith('R_'):foot=foot or 'Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal'
  if lib=='BAS70_V':foot=foot or 'Package_TO_SOT_SMD:SOT-23';data='https://assets.nexperia.com/documents/data-sheet/BAS70.pdf'
  if lib=='TLV9062_D':foot=foot or 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm';data='https://www.ti.com/lit/ds/symlink/tlv9062.pdf'
  dx=5.08 if lib.endswith('_V') else 0
  ry=y-3.81 if not lib.endswith('_V') else y-1.27
  vy=y+3.81 if not lib.endswith('_V') else y+1.27
  if lib.startswith('Conn'):dx=2.54;ry=y-7.62;vy=y+7.62
  if lib=='TLV9062_D':dx=0;ry=y-8.89;vy=y+8.89
  if lib=='TLV9062_D' and unit==3:dx=0;ry=y-15.24;vy=y+15.24
  self.items.append(f'(symbol (lib_id "AnalogDraft:{lib}") (at {x} {y} 0) (unit {unit}) (exclude_from_sim no) (in_bom {"no" if ref.startswith("#") else "yes"}) (on_board {"no" if ref.startswith("#") else "yes"}) (dnp no) (uuid {q(identifier)}) {prop("Reference",ref,x+dx,ry,ref.startswith("#"),lib.endswith("_V"))} {prop("Value",value,x+dx,vy,ref.startswith("#"),lib.endswith("_V"))} {prop("Footprint",foot,x,y,True)} {prop("Datasheet",data,x,y,True)} (instances (project {q(self.name)} (path {q("/"+self.uuid)} (reference {q(ref)}) (unit {unit})))))')
  if not ref.startswith('#') and not any(p['Reference']==ref for p in self.parts):self.parts.append({'Reference':ref,'Value':value,'Footprint':foot,'Datasheet':data,'Notes':note})
 def named_end(self,name,x,y,length=7.62):self.wire(x,y,x+length,y);self.label(name,x+length,y)
 def connector(self,ref,n,x,y,nets,value):
  self.component('Conn'+str(n),ref,value,x,y)
  for i,net in enumerate(nets):
   yy=y-((n-1)/2-i)*2.54;self.wire(x-5.08,yy,x-22.86,yy);self.label(net,x-22.86,yy)
 def flag(self,ref,net,x,y):self.component('PWR_FLAG',ref,'PWR_FLAG',x,y);self.wire(x,y,x+7.62,y);self.label(net,x+7.62,y)
 def cap_v(self,ref,value,x,y,top,bottom='AGND'):
  self.component('C_V',ref,value,x,y,note='Package/MPN TBD before assembly; non-polar')
  self.named_end(top,x,y-5.08);self.named_end(bottom,x,y+5.08)
 def clamp(self,ref,x,y,cathode,anode):
  self.component('BAS70_V',ref,'BAS70 candidate',x,y,note='Pin 2 is NC. Not validated fault protection.')
  self.named_end(cathode,x,y-5.08);self.named_end(anode,x,y+5.08)
 def finish(self):
  folder=ROOT/'schematics';folder.mkdir(exist_ok=True)
  content=f'(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0") (uuid {q(self.uuid)}) (paper "A4") (title_block (title {q(self.title)}) (date "2026-10-10") (rev "DRAFT-0.1") (company "stm32_motortester") (comment 1 "LOW VOLTAGE ONLY. NO BLUE PILL OR ST-LINK PINOUT ASSERTED.") (comment 2 "Candidate calculation. Physical verification and Critic-A review pending.")) (lib_symbols '+''.join(LIB[k] for k in sorted(self.used))+')\n'+'\n'.join(self.items)+'\n(embedded_fonts no))\n'
  (folder/(self.name+'.kicad_sch')).write_text(content)
  (folder/(self.name+'.kicad_pro')).write_text(json.dumps({'meta':{'filename':self.name+'.kicad_pro','version':1},'schematic':{'annotate_start_num':0},'erc':{'erc_exclusions':[]}},indent=2)+'\n')
  with (ROOT/(self.name+'-bom.csv')).open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=['Reference','Value','Footprint','Datasheet','Notes']);w.writeheader();w.writerows(self.parts)

s=Sch('option-a-dc','Option A - LV DC fixture (DRAFT)')
s.text('DRAFT / LV-001 DC ONLY. One selected tap, one ADC channel.\nJ1/J2/J3 pin numbers are LOCAL to this fixture; board mapping is TBD.',15.24,17.78,1.5)
s.connector('J1',2,35.56,40.64,['V3V3_VERIFIED','AGND'],'Verified shared rail')
x=68.58
for i,y in enumerate([40.64,60.96,81.28,101.6],1):s.component('R_V','R'+str(i),'1k 1%',x,y)
s.wire(x,35.56,x,30.48);s.named_end('V3V3_VERIFIED',x,30.48)
for y,net in [(50.8,'TAP75'),(71.12,'TAP50'),(91.44,'TAP25')]:
 s.wire(x,y-5.08,x,y);s.wire(x,y,x,y+5.08);s.named_end(net,x,y);s.junction(x,y)
s.wire(x,106.68,x,111.76);s.named_end('AGND',x,111.76)
s.connector('J2',4,129.54,63.5,['TAP25','TAP50','TAP75','DC_SELECT'],'Select ONE tap wire')
s.component('R_H','R5','1k 1%',160.02,63.5)
s.wire(144.78,63.5,154.94,63.5);s.label('DC_SELECT',144.78,63.5)
s.wire(165.1,63.5,185.42,63.5);s.label('ADC_IN',185.42,63.5)
s.cap_v('C1','10n C0G 5%',185.42,88.9,'ADC_IN')
s.clamp('D1',220.98,40.64,'V3V3_VERIFIED','ADC_IN')
s.clamp('D2',220.98,88.9,'ADC_IN','AGND')
s.connector('J3',3,269.24,127,['V3V3_VERIFIED','AGND','ADC_IN'],'ADC interface')
s.flag('#FLG01','V3V3_VERIFIED',35.56,124.46);s.flag('#FLG02','AGND',68.58,124.46)
s.text('Nominal 3.3 V: taps 0.825 / 1.650 / 2.475 V. Worst Rsource = 2k.\nNo external PSU or ignition signal enters this fixture. Disconnect power before tap changes.\nD1/D2 only candidate secondary clamps: rail back-power and -0.41 V / rail+0.41 V remain unresolved.\n3.3 V rail source, power budget, grounds and actual ADC header must be verified first.\nC1 adds settling memory: see calculate.py and README. No anti-alias accuracy claim.',15.24,142.24,1.27)
s.finish()

s=Sch('option-b-ac','Option B - LV AC buffer (DRAFT)')
s.text('DRAFT / LV LAB SOURCE ONLY. Source stays within -0.5 to +0.5 V, including transients.\nAll supply and ground connections belong to one VERIFIED domain. Never drive while unpowered.',15.24,17.78,1.5)
s.connector('J1',2,35.56,48.26,['LV_INPUT','AGND'],'Lab input only')
s.component('C_H','C1','1u film 16V',60.96,48.26,note='MPN and package TBD; not a 24V input rating')
s.wire(45.72,48.26,55.88,48.26);s.label('LV_INPUT',45.72,48.26)
s.component('R_H','R1','10k 1%',86.36,48.26);s.wire(66.04,48.26,81.28,48.26)
s.wire(91.44,48.26,139.7,48.26);s.label('BIAS_SIGNAL',99.06,48.26)
s.component('R_V','R2','100k 1%',101.6,78.74);s.named_end('BIAS_SIGNAL',101.6,73.66);s.named_end('VBIAS',101.6,83.82)
s.cap_v('C2','2.2n C0G 5%',132.08,78.74,'BIAS_SIGNAL')
s.component('TLV9062_D','U1','TLV9062IDR',149.86,50.8,2)
s.wire(160.02,50.8,167.64,50.8);s.wire(167.64,50.8,167.64,68.58);s.wire(167.64,68.58,134.62,68.58);s.wire(134.62,68.58,134.62,53.34);s.wire(134.62,53.34,139.7,53.34);s.junction(167.64,50.8)
s.component('R_H','R5','1k 1%',187.96,50.8);s.wire(167.64,50.8,182.88,50.8);s.wire(193.04,50.8,208.28,50.8);s.label('ADC_IN',208.28,50.8)
s.cap_v('C3','10n C0G 5%',205.74,78.74,'ADC_IN')
s.clamp('D1',40.64,86.36,'V3V3_VERIFIED','BIAS_SIGNAL');s.clamp('D2',71.12,86.36,'BIAS_SIGNAL','AGND')
s.clamp('D3',246.38,35.56,'V3V3_VERIFIED','ADC_IN');s.clamp('D4',246.38,78.74,'ADC_IN','AGND')
s.component('R_V','R3','10k 1%',35.56,119.38);s.named_end('V3V3_VERIFIED',35.56,114.3);s.named_end('VMID_RAW',35.56,124.46)
s.component('R_V','R4','10k 1%',35.56,147.32);s.named_end('VMID_RAW',35.56,142.24);s.named_end('AGND',35.56,152.4)
s.cap_v('C4','1u X7R 10%',63.5,139.7,'VMID_RAW')
s.component('TLV9062_D','U1','TLV9062IDR',104.14,124.46,1)
s.wire(78.74,121.92,93.98,121.92);s.label('VMID_RAW',78.74,121.92)
s.wire(114.3,124.46,121.92,124.46);s.wire(121.92,124.46,121.92,144.78);s.wire(121.92,144.78,88.9,144.78);s.wire(88.9,144.78,88.9,127);s.wire(88.9,127,93.98,127);s.named_end('VBIAS',121.92,124.46);s.junction(121.92,124.46)
s.component('TLV9062_D','U1','TLV9062IDR',154.94,132.08,3);s.named_end('V3V3_VERIFIED',154.94,121.92);s.named_end('AGND',154.94,142.24)
s.cap_v('C5','100n X7R',190.5,127,'V3V3_VERIFIED');s.cap_v('C6','1u X7R',218.44,127,'V3V3_VERIFIED')
s.connector('J2',3,274.32,129.54,['V3V3_VERIFIED','AGND','ADC_IN'],'ADC interface')
s.flag('#FLG01','V3V3_VERIFIED',172.72,152.4);s.flag('#FLG02','AGND',218.44,152.4)
s.text('R2 loads a capacitive sensor; this circuit DOES NOT preserve an unknown ignition plateau.\nNominal low-voltage model: gain ~0.909, HP ~1.45 Hz, LP ~7.96 kHz plus ~15.9 kHz.\nD1-D4 do not prove fault protection. No hot-plug/unpowered-input/24V/HV rating.\nLocal pin numbers only. Rail current, clamp recovery, stability, ground paths and board mapping TBD.',15.24,165.1,1.27)
s.finish()
libtext = '(kicad_symbol_lib (version 20241209) (generator \"kicad_symbol_editor\") (generator_version \"9.0\") '+''.join(v.replace('\"AnalogDraft:', '\"',1) for v in LIB.values())+')\n'
(ROOT/'schematics'/'AnalogDraft.kicad_sym').write_text(libtext)
(ROOT/'schematics'/'sym-lib-table').write_text('(sym_lib_table (version 7) (lib (name \"AnalogDraft\") (type \"KiCad\") (uri \"${KIPRJMOD}/AnalogDraft.kicad_sym\") (options \"\") (descr \"Local candidate primitives; no PCB release\")))\n')
print('Generated option-a-dc and option-b-ac (KiCad sources, projects and BOMs).')
