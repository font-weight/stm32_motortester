#!/usr/bin/env python3
import argparse,json,os,stat,subprocess,sys,time,types
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--decoder',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--fixture',type=Path,required=True);a=p.parse_args();o=a.output.resolve();o.mkdir(exist_ok=False);d=a.decoder.resolve();ns={'__name__':'filetype_target'};exec(compile(d.read_bytes(),str(d),'exec'),ns);rows=[]
fixture=a.fixture.resolve();identity=json.loads((fixture.parent/'canonical.json').read_text())['raw'];opts=['--build',str(identity['firmware_build']),'--generation',str(identity['generation']),'--nonce',str(identity['nonce_low']|(identity['nonce_high']<<32))]
fifo=o/'no-writer.pipe';os.mkfifo(fifo)
link=o/'dump-link.bin';link.symlink_to(fixture)
linkfifo=o/'fifo-link.pipe';linkfifo.symlink_to(fifo)
for name,path in [('fifo',fifo),('directory',o),('symlink',link),('symlink_to_fifo',linkfifo),('nonexistent',o/'absent'),('regular',fixture)]:
 argv=[sys.executable,'-I','-B',str(d),str(path),str(fixture),*opts];start=time.monotonic()
 try:
  q=subprocess.run(argv,capture_output=True,text=True,timeout=2);row=dict(name=name,argv=argv,returncode=q.returncode,stdout=q.stdout,stderr=q.stderr,seconds=time.monotonic()-start,pass_=q.returncode==(0 if name=='regular' else 2))
 except subprocess.TimeoutExpired as exc: row=dict(name=name,argv=argv,timeout=True,seconds=time.monotonic()-start,pass_=False)
 rows.append(row)
# Simulated lstat modes test special-file filtering without opening any device.
class MockPath:
 def __init__(self,mode):self.mode=mode
 def lstat(self):return types.SimpleNamespace(st_mode=self.mode)
for mode,name in [(stat.S_IFCHR,'char_device_mock'),(stat.S_IFBLK,'block_device_mock'),(stat.S_IFSOCK,'socket_mock'),(stat.S_IFLNK,'symlink_mock')]:
 try:ns['read_dump'](MockPath(mode));row=dict(name=name,pass_=False,actual='not rejected')
 except OSError as exc:row=dict(name=name,pass_=True,actual=str(exc))
 except Exception as exc:row=dict(name=name,pass_=False,actual=repr(exc))
 rows.append(row)
# A fake before-stat saying regular, followed by real FIFO open, must not hang.
class RacePath:
 def lstat(self):return fixture.stat()
 def __fspath__(self):return str(fifo)
try:ns['read_dump'](RacePath());rows.append(dict(name='regular_to_fifo_race',pass_=False))
except OSError as exc:rows.append(dict(name='regular_to_fifo_race',pass_=True,actual=str(exc)))
# Final symlink race must fail rather than follow replacement path.
class SymlinkRacePath(RacePath):
 def __fspath__(self):return str(link)
try:ns['read_dump'](SymlinkRacePath());rows.append(dict(name='regular_to_symlink_race',pass_=False))
except OSError as exc:rows.append(dict(name='regular_to_symlink_race',pass_=True,actual=str(exc)))
(o/'results.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps(dict(passed=sum(r['pass_'] for r in rows),failed=sum(not r['pass_'] for r in rows))));sys.exit(any(not r['pass_'] for r in rows))
