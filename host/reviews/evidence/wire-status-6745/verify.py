"""Read-only documentation-delta verification for DOC006 exact published SHA."""
import subprocess,json,re,hashlib,struct,zlib
from pathlib import Path
R=Path('/workspace/scratch/d2a4dc03255e/motortester');O=R/'host/reviews/evidence/wire-status-6745'
B='c54bd955aaf3cb863ef3df043ea237ea62e74ad4';H='6745fb0fa9802a510b49980e597e5f3679e025b8'
P21='8dfc32b2c7558624e8d02dde49404501e3fc2622';P30='a451e5a491439fcfed31f298a589643cf7050ec9';P35='0ddea1e5992caa6231203838c3956e19675867a8'
def git(*args):return subprocess.check_output(['git',*args],cwd=R)
def blob(sha,p):return git('show',sha+':'+p)
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
def tree(sha):
 return {line.split(b'\t',1)[1].decode():line.split(b'\t',1)[0].decode() for line in git('ls-tree','-r',sha).splitlines()}
a,b=tree(B),tree(H);require(a.keys()==b.keys(),'Paths changed')
changed=[p for p in a if a[p]!=b[p]];require(changed==['protocol/vectors-v0.1.json','protocol/wire-v0.1.md'],'Unexpected tree delta')
require(git('show','-s','--format=%P',H).decode().strip()==B,'Wrong parent')
for p in a:require(a[p].split()[0]==b[p].split()[0],'Mode change')
for p in b:require((Path('/tmp/critic-b-status-6745')/p).read_bytes()==blob(H,p),'Archive mismatch')
base=blob(B,'protocol/wire-v0.1.md').decode();head=blob(H,'protocol/wire-v0.1.md').decode()
fragments=[
('авторские тесты подготовлены, но не выполнены.', 'native-проверки выполнены, опубликованные свидетельства и их границы перечислены выше.'),
('Дальнейшие обязательные результаты: тактирование/ADC, карта выводов, допустимые Fs/window/pretrigger, реализация payloads и состояний, независимые векторы и реальные compile/tests, два проходных заключения критиков. Неполнота этого draft не уменьшает требования MVP.', 'До общей приёмки необходимо свести точные версии реализации payloads/состояний, независимых векторов и выполненных compile/tests в единый проверенный результат; отдельно подтвердить тактирование/ADC, карту выводов и допустимые Fs/window/pretrigger. Нужны два проходных заключения критиков на соответствующий результат. Уже выполненные ограниченные проверки перечислены выше; они не уменьшают требования MVP.'),
('Это требования к будущей реализации, текущий codec не объявляет capabilities.', 'Сам byte codec не объявляет capabilities; черновой контроллер PR35 реализует объявление поддерживаемого аналогового подмножества. Поддержка external trigger в этом контроллере не заявлена.'),
('Этот PR пока содержит только byte codec и один вектор; эти критерии открыты.', 'В файле vectors-v0.1.json сохранён один исходный codec-вектор; более широкие исполняемые проверки опубликованы отдельно по ссылкам выше. Полноту общих payload-векторов и совместных сценариев проверяют оба критика на конкретных SHA. Эти критерии нельзя считать закрытыми только по количеству тестов.')]
left=base.split('## Framing',1)[1];right=head.split('## Framing',1)[1]
for i,(old,new) in enumerate(fragments):
 require(left.count(old)==1 and right.count(new)==1,'Status fragment count mismatch')
 left=left.replace(old,f'<STATUS{i}>');right=right.replace(new,f'<STATUS{i}>')
require(left==right,'Normative text delta beyond four status fragments')
x=blob(B,'protocol/vectors-v0.1.json');y=blob(H,'protocol/vectors-v0.1.json')
xj=json.loads(x);yj=json.loads(y);xj.pop('status');yj.pop('status');require(xj==yj,'JSON semantic delta')
require([l for l in x.splitlines(keepends=True) if not l.startswith(b'  "status":')]==[l for l in y.splitlines(keepends=True) if not l.startswith(b'  "status":')],'JSON byte delta')
# Independent standard-library CRC and COBS decoding, no project imports.
v=yj['vectors'][0];frame=bytes.fromhex(v['frame_hex']);require(frame[-1]==0,'Delimiter');data=frame[:-1];out=bytearray();i=0
while i<len(data):
 code=data[i];require(code>0 and i+code<=len(data),'COBS length');out+=data[i+1:i+code];i+=code
 if code<255 and i<len(data):out.append(0)
raw=bytes(out);require(raw.hex()==v['decoded_hex'],'COBS bytes');require(zlib.crc32(raw[:-4])==int.from_bytes(raw[-4:],'little'),'CRC')
require(zlib.crc32(yj['crc_check']['ascii'].encode())==int(yj['crc_check']['hex'],16),'CRC check anchor')
major,minor,kind,reserved,length,reserved2,session,seq,req=struct.unpack('<BBBBHHQII',raw[:24]);e=v['expected']
require((major,minor,kind,session,seq,req,raw[24:-4].hex())==(e['major'],e['minor'],e['type'],int(e['session_hex'],16),e['sequence'],e['request'],e['payload_hex']),'Expected decoded fields')
require(not reserved and not reserved2 and length==len(raw)-28,'Reserved or length')
p21=json.loads(blob(P21,'tests/conformance/evidence/native-20261010/report.json'))
comparisons={}
for p,row in p21['source_files'].items():
 require(git('rev-parse',B+':'+p).decode().strip()==row['git_blob_sha'],'PR21 input differs from PR14')
 comparisons[p]={'blob_sha':row['git_blob_sha'],'sha256':hashlib.sha256(blob(B,p)).hexdigest()}
require(len(comparisons)==6,'Expected six codec/stream/command inputs')
require(all(s['exit_code']==0 for s in p21['stages']),'Published stage failed')
for name in ['strict-run','sanitizer-run']:
 require('3294024 assertions' in next(s for s in p21['stages'] if s['name']==name)['output'],'Assertion count differs')
# Session native evidence is independent of the six PR21 inputs.
native=blob(B,'docs/evidence/a004-native.txt').decode();session_hashes={}
for digest,p in re.findall(r'^([a-f0-9]{64})  (.+)$',native,re.M):
 require(hashlib.sha256(blob(B,p)).hexdigest()==digest,'Session native source hash mismatch')
 session_hashes[p]=digest
require(len(session_hashes)==6,'Expected six native session hash entries')
control=json.loads(blob(P35,'tests/control/independent/run-final/independent-wire.json'))
require((control['result'],control['full_windows'],control['samples'])==('PASS',20,40960),'Published control oracle mismatch')
control_log=blob(P35,'tests/control/independent/run-final/native.log').decode();require('RESULT 39 passed; 0 failed; 576994 checks' in control_log,'Control case count mismatch')
for sha,p in [(P30,'firmware/app/README.md'),(P30,'firmware/app/mt_capture_link.c'),(P35,'firmware/app/CONTROL_README.md'),(P35,'firmware/app/mt_control.c')]:
 require(len(blob(sha,p))>0,'Missing linked source')
require('firmware/app/mt_capture_link.c' not in a and 'firmware/app/mt_control.c' not in a,'Later modules incorrectly in base')
# Existing relative Markdown links must resolve at exact head.
links=re.findall(r'\]\(([^)]+)\)',head);relative=[]
for target in links:
 if not target.startswith('https://'):
  relative.append(target);require((Path('/tmp/critic-b-status-6745/protocol')/target.split('#')[0]).exists(),'Missing relative link '+target)
result={'base_sha':B,'head_sha':H,'tree_sha':git('rev-parse',H+'^{tree}').decode().strip(),'paths':len(a),'unchanged_blobs_and_modes':len(a)-2,'changed_paths':changed,'archive_equal_to_git':True,'normative_suffix_equal_after_only_four_status_substitutions':True,'status_substitutions':[{'before':x,'after':y} for x,y in fragments],'json_without_status_semantically_and_byte_identical':True,'original_vector_cobs_crc_and_fields_verified':True,'pr21_inputs_equal_to_pr14':comparisons,'pr21_all_stage_exit_codes_zero':True,'pr21_assertions_per_run':3294024,'session_native_hashes_match_base':session_hashes,'pr35_published_scenarios':39,'pr35_published_oracle_windows':20,'pr35_published_oracle_samples':40960,'pr30_pr35_modules_absent_from_base':True,'relative_links_resolved':relative,'changed_sha256':{p:hashlib.sha256(blob(H,p)).hexdigest() for p in changed},'personally_executed':'documentation/source/hash/vector checks only; no new C/ARM/USB/Windows execution'}
(O/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
(O/'delta.patch').write_bytes(git('diff',B,H))
(O/'pr21-native-report.json').write_bytes(blob(P21,'tests/conformance/evidence/native-20261010/report.json'))
(O/'pr14-session-native.txt').write_text(native)
(O/'pr35-oracle.json').write_text(json.dumps(control,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
