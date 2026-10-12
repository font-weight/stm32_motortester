#!/usr/bin/env python3
"""Check exported KiCad connectivity against the two candidate circuit intents."""
from pathlib import Path
import re,json
ROOT=Path(__file__).resolve().parents[1]
def parse(s):
 tokens=iter(re.findall(r'"(?:\\.|[^"\\])*"|\(|\)|[^\s()]+',s))
 def walk(tok):
  if tok=='(':
   v=[]
   for t in tokens:
    if t==')':return v
    v.append(walk(t))
   raise ValueError('Unclosed s-expression')
  return json.loads(tok) if tok.startswith('"') else tok
 return walk(next(tokens))
def sub(lst,name):return next(x for x in lst if isinstance(x,list) and x and x[0]==name)
def nets(path):
 tree=parse(path.read_text());ans={}
 for n in sub(tree,'nets')[1:]:
  name=sub(n,'name')[1];points=set()
  for x in n:
   if isinstance(x,list) and x and x[0]=='node':points.add((sub(x,'ref')[1],sub(x,'pin')[1]))
  ans[name]=points
 return ans
expected={
'option-a-dc':{
 '/ADC_IN':{('C1','1'),('D1','1'),('D2','3'),('J3','3'),('R5','2')},
 '/AGND':{('C1','2'),('D2','1'),('J1','2'),('J3','2'),('R4','2')},
 '/DC_SELECT':{('J2','4'),('R5','1')},
 '/TAP25':{('J2','1'),('R3','2'),('R4','1')},
 '/TAP50':{('J2','2'),('R2','2'),('R3','1')},
 '/TAP75':{('J2','3'),('R1','2'),('R2','1')},
 '/V3V3_VERIFIED':{('D1','3'),('J1','1'),('J3','1'),('R1','1')}},
'option-b-ac':{
 '/ADC_IN':{('C3','1'),('D3','1'),('D4','3'),('J2','3'),('R5','2')},
 '/AGND':{('C2','2'),('C3','2'),('C4','2'),('C5','2'),('C6','2'),('D2','1'),('D4','1'),('J1','2'),('J2','2'),('R4','2'),('U1','4')},
 '/BIAS_SIGNAL':{('C2','1'),('D1','1'),('D2','3'),('R1','2'),('R2','1'),('U1','5')},
 '/LV_INPUT':{('C1','1'),('J1','1')},
 '/V3V3_VERIFIED':{('C5','1'),('C6','1'),('D1','3'),('D3','3'),('J2','1'),('R3','1'),('U1','8')},
 '/VBIAS':{('R2','2'),('U1','1'),('U1','2')},
 '/VMID_RAW':{('C4','1'),('R3','2'),('R4','1'),('U1','3')},
 'Net-(C1-Pad2)':{('C1','2'),('R1','1')},
 'Net-(U1B--)':{('R5','1'),('U1','6'),('U1','7')}}}
for variant,exp in expected.items():
 got=nets(ROOT/'evidence'/(variant+'.net'))
 assert got==exp, f'{variant}: connectivity mismatch: {got}'
 print(f'{variant}: all {len(exp)} nets exactly match candidate intent')
print('No electrical performance or physical safety is established by this check.')
