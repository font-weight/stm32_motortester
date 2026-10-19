# Черновая телеметрия первого STREAM опыта

Опция CFG_MT_STREAM_DIAGNOSTICS по умолчанию 0. В таком профиле вызовы наблюдения
удаляются при компиляции; отдельный mt_stream_diag.c не должен линковаться.
Явный --diagnostics в отдельном build_stream_draft.py включает модуль и
проверку DWT до runtime/USB init. Это отдельный инструментированный ELF,
30 link inputs и instrumentation_profile=foreground-dwt-v1. При недоступном
DWT boot завершается с BOOT_DIAGNOSTICS до допуска IRQ. Никаких измерений платы нет.

Предложенный профиль хранит только один фиксированный foreground RAM record,
без ISR writers, новых locks, printf, CDC logger и дополнительных вызовов TIME.
DWT CYCCNT используется только после проверки TRCENA, NOCYCCNT, CYCCNTENA,
readback и ограниченной проверки движения счётчика. CYCCNT не сбрасывается;
остальные биты DEMCR/DWT_CTRL сохраняются. Текущее состояние controls проверяется
при наблюдении. Остановка счётчика/отключение даёт sticky invalid flag.

Основание: Arm Cortex-M3 TRM, разделы 10.2 и 11.5:
https://www.keil.com/dd/docs/datashts/arm/cortex_m3/r2p0/ddi0337g_cortex_m3_r2p0_trm.pdf
CYCCNT 32-битный, с переполнением, и не учитывает debugger-halted время.
Запись не доказывает отсутствие прежней остановки отладчиком или полного
пропущенного оборота. Для первого короткого опыта обязательны отсутствие
breakpoints/step/live watch, отдельное HOST время и интервал меньше оборота.
При номинальных 72 МГц оборот около 59,65 с; это расчёт, не измерение частоты.

После валидации первого START nonce и generation сохраняются перед preliminary
quiesce, даже если этот барьер завершится отказом до аппаратного start.
Отклонённая команда START не начинает запись. При неподтверждённом hardware_off
запись остаётся active, а не получает ложный frozen. Наблюдаются:
- span тела runtime step от внутреннего stamp до чтения observe, включая preemption;
- промежутки между этими stamp, включая telemetry bookkeeping и хвост возврата;
- TX bracket до lock и после unlock, включая финальные stores и возможный IRQ;
- реальные ADC polls, включая EMPTY: existing stamp-last_poll_us и отдельные cycles;
- existing end-stamp для copy/validation, без дополнительного TIME update;
- queue high-water и первая наблюдённая причина до TX/очистки нового HELLO.

step_body_max_cycles исключает входные проверки до stamp, последующее
telemetry bookkeeping, r->in_step=0 и возврат: это не полная длительность call.
Первый step включает часть работы до START. Первый poll_gap_cycles отсутствует
до второго poll; poll_gap_us уже включает интервал от существующего start stamp.
Нули без соответствующего count не означают проверенную нулевую длительность.
Copy count включает попытки копирования, которые последующая проверка отвергнет.
Счётчики насыщаются, saturation отмечается. Дельты cycles имеют modulo-семантику
и условие интервала меньше 2^32 cycles; они не являются alias-proof временем.

Первый record замораживается только после фактического hardware_off,
ADC STOPPED и отсутствия владения, при конечном состоянии протокола.
Для нормального Stop это включает END/STOP_ACK передачу в FIFO. Приём HOST
доказывается отдельно его raw capture; FIFO acceptance не доказывает доставку.
Новый HELLO при quiescent состоянии замораживает прежнюю запись с replacement
flag до commit, чтобы не смешивать разные сеансы. Последний step тогда неполон.
В pre-TX first_runtime_result может быть UINT32_MAX: возврат step ещё неизвестен.

После frozen record не изменяется. Извлекать его через SWD следует только после
остановки опыта; прочитать дважды и проверить идентичность, magic/version/bytes,
точный symbol map и firmware identity. Структура v1 содержит 34 uint32 (136 байт).
Текущая реализация и точки наблюдения ещё проходят native/независимое ревью.

Telemetry добавляет накладные расходы и принадлежит только instrumented ELF.
Она не доказывает qualified B, отдельный IRQ-off интервал, частоту/точность АЦП,
время USB-транзакций или будущий worst case. Никаких численных гарантий по ней
не назначать. Старый uninstrumented stack/ELF proof требует отдельной привязки
к новому образу; отсутствие overflow не заменяет статическую проверку стека.
