# Critic-B: независимое ревью DRAFT framing A-001

Дата: 10.10.2026. Репозиторий: [font-weight/stm32_motortester](https://github.com/font-weight/stm32_motortester). [Issue #3](https://github.com/font-weight/stm32_motortester/issues/3), [PR #4](https://github.com/font-weight/stm32_motortester/pull/4).

**Вердикт для предъявленного A-001: `BLOCKED_EVIDENCE`, `score: null`.** Byte-codec прошёл перечисленные ниже независимые проверки. Требуемые карточкой A-001 потоковые сценарии не представлены. Это не утверждение о дефекте COBS/CRC и не требование реализовать весь MVP в PR с codec.

## Версия и независимость

- Проверенный head: `18f154f3e864dc30ab8d56fd78c1266023df883a`.
- Base: `3fe1a2d8c53f77e0510a3da9ddd125f68cb872fa`.
- Diff: только `protocol/wire-v0.1.md`, `protocol/vectors-v0.1.json`, `firmware/protocol/mt_wire.h`, `firmware/protocol/mt_wire.c`.
- Critic-B: `gpt-6-astra`, `xhigh`, отдельный от автора реализации агент. Production-код, продуктовые тесты и ветка не изменялись. Исходники извлечены через `git show` точного SHA в отдельную временную папку; проверки написаны и выполнены независимо.
- На чтении PR около 11:54 UTC текущий head уже был `a81339d087fac4d77017a22dcce1f4fe11cfc46b`. Настоящий отчёт **не оценивает этот новый commit** и не переносится на него автоматически.

## Область и заранее назначенная приёмка

Уровень: документация/расчёт и программа/симулятор на Linux x86_64. Byte-codec принимает/выдаёт одно сообщение с 24-байтовым заголовком, payload 0..512, CRC и COBS; потоковое накопление ему намеренно не принадлежит.

Карточка #3 требует однозначных версии, endianness, длин, CRC, framing, timeout/resync; round-trip, отрицательных векторов, фактических host-компиляции и тестов, bounds/fragmentation/coalescing/corruption и неизменности выходов при отказе. Эти условия прочитаны непосредственно из карточки, а не добавлены после проверки. Основания: [ТЗ MVP §3/8/9](../../docs/mvp-spec.md), [FR-05/06 и DOC-02](../../requirements.md), [quality-gate v1.0](../../docs/quality-gate.md).

Payloads всех команд, HELLO/capabilities и режимы целиком в узкую проверку функции codec не входят. Их отсутствие, честно отмеченное как TBD, не называется дефектом самой функции. Оно препятствует отдельному утверждению общего host–firmware контракта.

## Приоритетные замечания и следующие действия

### E1 — блокер доказательств A-001: не проверены потоковые обязательства карточки

Место: `protocol/wire-v0.1.md:8,14–18,44–46`, публичная граница `mt_wire_decode`, критерии issue #3.

Воспроизведение: в проверенном diff есть функция разбора уже выделенного кадра, но нет накопителя и тестов, подающих один поток разными частями. Нельзя проверить timeout/отбрасывание до разделителя, восстановление после переполнения, игнорирование пустых разделителей или несколько кадров в одном чтении по вызову функции с одним готовым кадром. Мои negative/bounds-тесты ниже не закрывают эти сценарии.

Последствие: один и тот же корректный поток после разбиения USB-чтениями ещё не доказан эквивалентным; усечённый кадр и следующий исправный кадр не проверены в общем состоянии парсера. Это обязательные свидетельства **самой карточки A-001**, а не аппаратного MVP.

Действие: предъявить небольшой ограниченный потоковый слой/референсный harness и воспроизводимые тесты разбиения каждого контрольного кадра по всем границам, склейки нескольких кадров, пустых разделителей, overflow с восстановлением и timeout на границе `elapsed >= limit`. Это не требует USB-драйвера или аппаратуры. Если задачи разрезаются иначе, оформить явное изменение области с причиной и ревью, сохранив потоковые требования открытыми в следующей задаче; не выдавать прежний A-001 за выполненный.

### E2 — следующий отдельный контракт: host не может реализовать совместимый сеанс по этому draft

Место: `protocol/wire-v0.1.md:48–52,60–62`.

Числа команд, layouts и ограничения payload, HELLO response/capabilities, обработка повторного HELLO/ACK/retry, принятые настройки и порядок terminal event/Stop ACK пока TBD. Тип 127 в единственном векторе является неприменимой командой и верно назван opaque.

Последствие: host может экспериментально проверить byte-framing, но не сформировать согласованную команду или интерпретировать захват; общую совместимость объявлять нельзя. Область HOST-001 с внутренним заменяемым источником остаётся независимой.

Действие: следующей совместной фиксацией A+B задать минимальный обмен HELLO/capabilities/configure/capture data/status/error, точные поля/единицы и общие примеры. Для окна согласовать единицу `offset/count`, endian 16-битных кодов, максимальное число отсчётов на блок с учётом его метаданных, область CRC исходного массива и происхождение времени/номинальной Fs. Разрешить порядок Stop/terminal и правила retry до реализации этих состояний. Это условие **приёмки общего протокола**, а не требование расширить этот codec PR всем MVP.

### N1 — сопровождение проверок

В `vectors-v0.1.json:7–23` на проверенном SHA один положительный вектор. Независимый воспроизводимый набор ниже даёт дополнительные свидетельства, но временный reviewer-run не создаёт автоматически поддерживаемый regression suite команды A. Стоит закрепить положительные, отрицательные и граничные conformance-векторы/команды в принадлежащей A области тестов и прогонять их на каждом следующем изменении codec. Сам по себе низкий счётчик векторов не причина снижать оценку; важны покрываемые требования.

**P0/P1-дефектов алгоритма байтового encoder/decoder в проведённых проверках не обнаружено.** E1 — отсутствие обязательных доказательств, а не обнаруженная ошибка алгоритма. Существующие P0/P1 не скрываются успешным fuzz-проходом; проверка не доказывает отсутствие всех возможных дефектов.

## Проверено лично

Среда: Linux x86_64, Python 3.12.14, GCC 14.2.0. Host-компиляция C11 с `-O2 -Wall -Wextra -Werror -Wpedantic -Wconversion -Wsign-conversion` прошла без предупреждений. Проверки использовали независимые `struct.pack('<BBBBHHQII', ...)`, `zlib.crc32` и COBS через разбиение по нулям/254-байтовым блокам, а не копию mark/code-цикла из C.

- CRC `123456789` → `0xcbf43926`, пустой CRC → 0.
- Опубликованный raw/frame вектор совпадает побайтово; значения полей и payload верны.
- 2052 сравнения encode/decode с независимым ожидаемым форматом: все длины payload 0..512, нули, 0xff, циклический pattern и детерминированные случайные данные; случайные ID/type.
- 544 недостаточные выходные ёмкости 0..543: отказ без изменения destination и `written`; payload 513 также отвергается до записи.
- 8 структурных отказов с пересчитанным CRC: неверные major/minor/reserved/длины; 736 однобитных повреждений raw; 93 усечения одного контрольного COBS-кадра; 7 ошибочных length/framing-сценариев.
- 100000 детерминированных случайных некорректных кадров: отказ, неизменный выход; seed `20261010`.
- AddressSanitizer/UndefinedBehaviorSanitizer: 200000 итераций случайного decode и успешного encode/decode, замечаний нет. Начальный запуск упёрся в известную для данной среды ошибку LeakSanitizer с ptrace; повтор выполнен с `ASAN_OPTIONS=detect_leaks=0`. Проверка утечек **не проведена**, ASan/UBSan проведены. Codec сам не выделяет heap.
- Первая версия reviewer-harness ожидала LENGTH для потока из 543 байт `0xff`; фактический FRAMING корректен из-за обрезанного последнего COBS-блока. Исправлено только ожидание harness; код PR не менялся.

Guard-области, неизменность входного message и выходного message при отказе проверялись. Эти числа описывают конкретный прогон, а не являются формулой оценки или доказательством Windows/USB/MCU.

SHA-256 исходников извлечённого commit:

- `mt_wire.c`: `84677c6054294cc1d3dbf7095352a265fc038288693e890f6673d911da5bdb8c`
- `mt_wire.h`: `8b75e5327ec8c1be97270195e514175f62c2051a636b83a0ae084f132d1486d2`
- `vectors-v0.1.json`: `aab674d82d9c769bb890ac7a5d3b3891370ef28d2b1a724a4a5f2b39d7439c44`

## Что не проверено

Потоковый parser/timeout/resync, dispatcher, session/retry, Windows 10/CDC, target ARM-компиляция и расход стека полной прошивки, USB платы, захват/режимы, аналоговый тракт и любые измерения. Возможность безопасного Stop относится к будущему согласованному автомату, а не подтверждена наличием абзаца. Проверка этого bounded C-кода на host не требует всей прошивки, Windows-пакета или лаборатории.

## Рубрика и вердикт

Применяется неизменная quality-gate v1.0. При неизвестном обязательном свидетельстве оценка не выдумывается.

- C1, 30%: численный балл не назначен. Наблюдение: ограниченный codec соответствует описанным endian/CRC/длинам и атомарности результата в проверенной матрице; E1 остаётся открытым для заявленной карточки.
- C2, 20%: численный балл не назначен. Bounds, corruption, неизменность выходов и sanitizer проверены; восстановление потока не проверено.
- C3, 25%: численный балл не назначен. Фактические воспроизводимые независимые проверки приложены; отсутствуют обязательные потоковые доказательства.
- C4, 15%: численный балл не назначен. Граница byte-codec ясна, фиксированные ресурсы и draft/TBD честно обозначены; эту границу важно сохранить в host.
- C5, 10%: численный балл не назначен. Версия/команды/исходники воспроизводимы по приложению; регрессионный набор и проверка нового head должны быть закреплены авторами.

Итог `score: null`, `BLOCKED_EVIDENCE` для предъявленной A-001 и отдельно для утверждения общего wire-контракта. Порог 9,5 и веса не изменены. Для закрытия E1 предъявить согласованную область и отсутствующие тесты, затем провести проверку актуального точного SHA. Общему решению требуются два независимых проходных вердикта; этот отчёт не заменяет Critic-A. Предыдущих замечаний по этому SHA у данного критика не было.

## Приложение: воспроизведение независимых проверок

Приложенные ниже скрипты — артефакт ревью, не изменение реализации или продуктовых тестов. Сохранить два блока как `check_wire.py` и `sanitize_wire.c` в отдельной временной папке вместе с извлечёнными файлами. Сначала из корня checkout:

```sh
T=$(mktemp -d)
SHA=18f154f3e864dc30ab8d56fd78c1266023df883a
for P in firmware/protocol/mt_wire.h firmware/protocol/mt_wire.c protocol/vectors-v0.1.json; do
  git show "$SHA:$P" > "$T/$(basename "$P")"
done
# Сохранить два следующих блока в $T, затем:
gcc -std=c11 -O2 -Wall -Wextra -Werror -Wpedantic -Wconversion -Wsign-conversion -fPIC -shared "$T/mt_wire.c" -o "$T/libmt_wire.so"
python "$T/check_wire.py"
gcc -std=c11 -g -O1 -Wall -Wextra -Werror -Wpedantic -Wconversion -Wsign-conversion -fsanitize=address,undefined -fno-omit-frame-pointer "$T/mt_wire.c" "$T/sanitize_wire.c" -o "$T/sanitize_wire"
ASAN_OPTIONS=detect_leaks=0 "$T/sanitize_wire"
```

`check_wire.py`, SHA-256 `854785709cb2b5e3c4310c4ec80727930aa40f0133b8d98097e193a94c9cf1ea`:

```python
import ctypes as C, json, random, struct, zlib
from pathlib import Path
root=Path(__file__).parent
lib=C.CDLL(str(root/'libmt_wire.so'))
class Message(C.Structure):
    _fields_=[('type',C.c_uint8),('session',C.c_uint64),('sequence',C.c_uint32),('request',C.c_uint32),('payload_length',C.c_uint16),('payload',C.c_uint8*512)]
lib.mt_wire_encode.argtypes=[C.POINTER(Message),C.POINTER(C.c_uint8),C.c_size_t,C.POINTER(C.c_size_t)]
lib.mt_wire_decode.argtypes=[C.POINTER(C.c_uint8),C.c_size_t,C.POINTER(Message)]
lib.mt_wire_crc32.argtypes=[C.POINTER(C.c_uint8),C.c_size_t]
lib.mt_wire_crc32.restype=C.c_uint32
counts={}
def note(k): counts[k]=counts.get(k,0)+1
def cbuf(b): return (C.c_uint8*len(b)).from_buffer_copy(b)
def cobs(raw):
    # Independent block decomposition, not the C encoder's rolling mark/code loop.
    blocks=[]
    for part in raw.split(b'\0'):
        while len(part)>=254:
            blocks.append(b'\xff'+part[:254]); part=part[254:]
        blocks.append(bytes([len(part)+1])+part)
    return b''.join(blocks)
def rawmsg(m):
    body=struct.pack('<BBBBHHQII',0,1,m.type,0,m.payload_length,0,m.session,m.sequence,m.request)+bytes(m.payload[:m.payload_length])
    return body+struct.pack('<I',zlib.crc32(body))
def message(payload,typ=127,session=0x0807060504030201,seq=1,req=2):
    m=Message(type=typ,session=session,sequence=seq,request=req,payload_length=len(payload));m.payload[:len(payload)]=payload;return m
def decode(frame,expected=None):
    m=Message(); C.memset(C.byref(m),0xa5,C.sizeof(m)); before=bytes(m)
    guard=(C.c_uint8*(len(frame)+32))(*([0xa5]*16+list(frame)+[0xa5]*16))
    ptr=C.cast(C.byref(guard,16),C.POINTER(C.c_uint8)); code=lib.mt_wire_decode(ptr,len(frame),C.byref(m))
    assert bytes(guard)==b'\xa5'*16+frame+b'\xa5'*16
    if code: assert bytes(m)==before,('output modified',code)
    if expected is not None: assert code==expected,(code,expected,frame.hex())
    return code,m
assert lib.mt_wire_crc32(cbuf(b'123456789'),9)==zlib.crc32(b'123456789')==0xcbf43926
assert lib.mt_wire_crc32(None,0)==0
v=json.loads((root/'vectors-v0.1.json').read_text())['vectors'][0]
m=message(bytes.fromhex(v['expected']['payload_hex']))
assert rawmsg(m).hex()==v['decoded_hex']
assert (cobs(rawmsg(m))+b'\0').hex()==v['frame_hex'];note('published_vector')
rng=random.Random(20261010)
for n in range(513):
    for payload in (bytes(n),b'\xff'*n,bytes(i%256 for i in range(n)),rng.randbytes(n)):
        m=message(payload,typ=rng.randrange(256),session=rng.getrandbits(64),seq=rng.getrandbits(32),req=rng.getrandbits(32)); before=bytes(m)
        guard=(C.c_uint8*576)(*([0xa5]*576));written=C.c_size_t(999)
        ptr=C.cast(C.byref(guard,16),C.POINTER(C.c_uint8))
        assert lib.mt_wire_encode(C.byref(m),ptr,544,C.byref(written))==0
        got=bytes(guard)[16:16+written.value];expected=cobs(rawmsg(m))+b'\0'
        assert got==expected,(n,got.hex(),expected.hex())
        assert bytes(guard)[:16]==bytes(guard)[560:]==b'\xa5'*16
        assert bytes(m)==before and 29<=written.value<=544
        _,opened=decode(got[:-1],0)
        assert (opened.type,opened.session,opened.sequence,opened.request,opened.payload_length,bytes(opened.payload[:n]))==(m.type,m.session,m.sequence,m.request,n,payload)
        note('encode_decode_oracle')
# All undersized output capacities must leave destination and written unchanged.
m=message(b'abc')
for cap in range(544):
    out=(C.c_uint8*544)(*([0xa5]*544));written=C.c_size_t(999)
    assert lib.mt_wire_encode(C.byref(m),out,cap,C.byref(written))==2
    assert bytes(out)==b'\xa5'*544 and written.value==999;note('capacity_rejection')
m.payload_length=513;out=(C.c_uint8*544)(*([0xa5]*544));written=C.c_size_t(999)
assert lib.mt_wire_encode(C.byref(m),out,544,C.byref(written))==4
assert bytes(out)==b'\xa5'*544 and written.value==999;note('oversized_encode')
raw=rawmsg(message(bytes(range(64))))
def framed_mutation(offset,value,repair=True):
    x=bytearray(raw);x[offset]=value
    if repair:x[-4:]=struct.pack('<I',zlib.crc32(x[:-4]))
    return cobs(bytes(x))
for offset,value,code in [(0,1,6),(1,2,6),(3,1,7),(6,1,7),(7,1,7),(4,63,4),(4,65,4),(5,3,4)]:
    decode(framed_mutation(offset,value),code);note('structured_rejection')
for offset in range(len(raw)):
    for bit in range(8):
        x=bytearray(raw);x[offset]^=1<<bit
        assert decode(cobs(bytes(x)))[0]!=0;note('single_bit_rejection')
frame=cobs(raw)
for i in range(len(frame)):
    assert decode(frame[:i])[0]!=0;note('truncation_rejection')
for frame,code in [(b'',4),(b'\0',3),(b'\x02',3),(b'\x02\0',3),(b'\xff'*543,3),(b'\x01'*544,4),(b'\x01'*545,4)]:
    decode(frame,code);note('framing_length_rejection')
for _ in range(100000):
    b=rng.randbytes(rng.randrange(0,545))
    code,_=decode(b)
    assert code!=0;note('random_rejection')
print(json.dumps({'status':'PASS','counts':counts,'seed':20261010,'compiler':'GCC 14.2.0','host':'Linux x86_64','scope':'byte codec only; no USB, stream state, handshake, Windows or hardware'},indent=2))
```

`sanitize_wire.c`, SHA-256 `c4d102262c9383e0a1d7960b141da42c711f894adaf6df35b7feb0e1df36fdfb`:

```c
#include "mt_wire.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
static uint32_t state=20261010;
static uint32_t rnd(void) { state^=state<<13;state^=state>>17;state^=state<<5;return state; }
int main(void) {
    uint8_t frame[MT_WIRE_FRAME_MAX];
    mt_wire_message m, result, before;
    size_t i,j,n,w;
    for(i=0;i<200000;i++) {
        n=rnd()%(MT_WIRE_FRAME_MAX+1u);
        for(j=0;j<n;j++)frame[j]=(uint8_t)rnd();
        memset(&result,0xa5,sizeof result);memcpy(&before,&result,sizeof result);
        mt_wire_result rc=mt_wire_decode(frame,n,&result);
        if(rc!=MT_WIRE_OK)assert(memcmp(&result,&before,sizeof result)==0);
        memset(&m,0,sizeof m);m.type=(uint8_t)rnd();m.session=((uint64_t)rnd()<<32)|rnd();m.sequence=rnd();m.request=rnd();
        m.payload_length=(uint16_t)(rnd()%513u);
        for(j=0;j<m.payload_length;j++)m.payload[j]=(uint8_t)rnd();
        assert(mt_wire_encode(&m,frame,sizeof frame,&w)==MT_WIRE_OK);
        assert(mt_wire_decode(frame,w-1,&result)==MT_WIRE_OK);
        assert(result.type==m.type && result.session==m.session && result.sequence==m.sequence && result.request==m.request && result.payload_length==m.payload_length);
        assert(memcmp(result.payload,m.payload,m.payload_length)==0);
    }
    return 0;
}
```
