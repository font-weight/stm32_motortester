#!/usr/bin/env python3
"""Disposable actual PR108 runtime adapter; no replacement host parser."""
import argparse, dataclasses, enum, hashlib, importlib, json, os, selectors, subprocess, sys, time
from pathlib import Path

def clean(v):
 if dataclasses.is_dataclass(v): return clean(dataclasses.asdict(v))
 if isinstance(v,enum.Enum): return v.value
 if isinstance(v,bytes): return v.hex()
 if isinstance(v,dict): return {k:clean(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)): return [clean(x) for x in v]
 return v

def require(c,m):
 if not c: raise ValueError(m)
def save(p,v): p.write_text(json.dumps(clean(v),indent=2)+'\n')
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
class Native:
 def __init__(self,exe,out):
  self.log=[];self.stderr=(out/'native.stderr').open('wb')
  self.proc=subprocess.Popen([str(exe)],cwd=out,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr)
 def op(self,cmd):
  row={'command':cmd,'response_hex':''};self.log.append(row)
  self.proc.stdin.write((cmd+'\n').encode());self.proc.stdin.flush();deadline=time.monotonic()+5;raw=bytearray()
  with selectors.DefaultSelector() as sel:
   sel.register(self.proc.stdout,selectors.EVENT_READ)
   while b'\n' not in raw:
    left=deadline-time.monotonic();require(left>0 and sel.select(left),'native timeout')
    x=os.read(self.proc.stdout.fileno(),4096);require(x,'native EOF');raw.extend(x);row['response_hex']=raw.hex();require(len(raw)<=65536,'native oversized')
  require(raw.endswith(b'\n') and raw.count(b'\n')==1,'native framing');v=json.loads(raw);require(type(v) is dict,'native object');row['reply']=v;return v
 def close(self):
  self.proc.stdin.close();code=self.proc.wait(timeout=5);require(code==0,'native exit');return code
 def dispose(self):
  if self.proc.poll() is None:self.proc.kill()
  self.proc.wait(timeout=5)
  for p in (self.proc.stdin,self.proc.stdout):
   if p and not p.closed:p.close()
  self.stderr.close()
class Link:
 def __init__(self,native,full=False):
  self.n=native;self.full=full;self.now=0;self.rx=bytearray();self.wire=bytearray();self.writes=[];self.reads=[];self.states=[];self.limit=4096;self.stop_te=False;self.fail_read=False
  self.accept(self.n.op('INIT'))
 def accept(self,d):
  self.states.append(d)
  if self.full:self.now=d['now_ms']
  b=bytes.fromhex(d['wire']);self.rx.extend(b);self.wire.extend(b);return d
 def op(self,cmd,now=None,drain=False):
  if now is not None:self.now=now
  d=self.accept(self.n.op(cmd))
  if drain:d=self.accept(self.n.op('POLL %d 64 150'%self.now))
  return d
 def write(self,b):
  self.writes.append(b)
  if self.full:
   op='STOP_TE ' if self.stop_te else 'RX ';self.stop_te=False;self.op(op+b.hex())
  else:self.op('RX %d %s'%(self.now,b.hex()),drain=True)
  return len(b)
 def read(self,size):
  if self.fail_read:self.reads.append({'error':'modeled physical transport failure'});raise OSError('modeled reset observed')
  b=bytes(self.rx[:min(size,self.limit)]);del self.rx[:len(b)];self.reads.append(b);return b
