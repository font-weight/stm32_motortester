# Native-проверка настоящего адаптера записи PR155 и PREPARE

Отдельный программный опыт, продолжение ограниченной проверки PR153.
A production: `c4d4dc2966656b324518c8d0061811d1241870a3`.
B: `dbbff7812a9d811d6764dfb80ad83001c8f10bdb`, tree
`412176cadae21859d677d08d130a654cbeb19643`. Файлы B не изменяются.
Ранее проверенная пара PR154 сохранена в PR156; её результат не переносится
автоматически. Здесь отдельно проверяется новый worker-side PREPARE.

## Что здесь новое

Вместо собственного исполнителя intents из PR153 используется настоящий
`RecordingSessionAdapter` B: его controller, runtime, accessor provenance,
дисковый поток, append, finish и Save. Этот модуль не зависит от Qt.
Старый `recording-session-reproducer` остаётся отдельной точной парой PR152.
Его native bridge и IPC helper переиспользуются побайтно, по manifest,
в исходных путях. Историческая fixture и её call boundary не подменяются.
Ни PR95 runtime/MMIO inquiry, ни PR147 proof не запускаются.

Ограничение предыдущего PR153: его custom adapter сохранял исходные полученные
отсчёты и переданные им метаданные, но в 80 full/partial entries четырёх вариантов
оставлял доступные BEGIN/terminal identities как `not_supplied`. Исходные wire
логи сохраняли эти факты отдельно. Тот результат не доказывал полноту
самостоятельного архива. Здесь проверяется новый точный production adapter,
а прежние результаты и оценки не переносятся автоматически.

## Запуск

Linux, Python3.11+ и уже установленный GCC с ASan/UBSan. Qt, установка пакетов,
сеть, ARM-компилятор, USB/COM и плата не требуются.

```sh
python3 -I -B /absolute/A/tests/recording-adapter-reproducer/reproduce.py \
  --a-root /absolute/A \
  --host-root /absolute/B/host \
  --output /absolute/existing-parent/new-evidence
```

Входные размеры/SHA256 перечислены в `inputs.json`; имя checkout не заменяет
проверку содержимого. Output новый, абсолютный, вне входов и без symlink.
Runner обычным Python создаёт strict и ASan/UBSan native-сборки, затем для каждой
запускает обычный и optimized probe. Проверки не зависят от Python assert.
Каждый процесс ограничен 180 секундами; native IPC — 5 секундами;
ожидание adapter ограничено 10 секундами и числом шагов. В цикле есть короткий
sleep, освобождающий выполнение дисковому потоку; это не измерение задержки UI.

## Матрица нового доказательства

По три последовательности в каждом из четырёх вариантов:

1. Один adapter/runtime последовательно записывает и сохраняет full END,
   BEGIN-known partial ABORT и no-BEGIN ABORT. Каждая запись имеет другой UUID,
   level_code и принятый config_id; session nonce остаётся прежним. Команды
   идут по одному байту. Между записями новый Record допускается только после
   успешного Save предыдущей. Это проверяет изоляцию поколений и конфигураций.
2. Auto: три полных окна с новым явным ARM каждого поколения, затем Stop и Save.
   Timeout и отсутствие trigger index проверяются как программные факты.
3. Single: одно полное signal-окно, автоматическое окончание и Save.

Настоящий adapter writer блокируется перед fsync. Проверяется identity его
потока: файловая операция выполняется на writer thread, не на transport owner.
Пока он удерживается, native/runtime продолжают pumping через 5400 мс модельного
времени; исходный объект и его immutable facts остаются у runtime. В Normal
подаётся пользовательский Stop. До receipt нового ARM нет. Barrier затем
пропускает оригинальный sync, без изготовления receipt. Копия исходного объекта
не принимается accessor; после локального ACK и в следующих поколениях старый
объект также не возвращает provenance.

После каждого Save отдельный COBS/header/CRC decoder сравнивает archive с
фактическими host/device bytes, не импортируя B wire decoder:

- nonce, generation, config_id и точный CONFIG через его matching CONFIG_ACK;
- BEGIN request/sequence и terminal kind/request/sequence;
- END CRC и все 2048 исходных кодов;
- full nominal Fs и kind/index/edge/level/pretrigger прямо по BEGIN;
- mode/timeout provenance прямо по CONFIG и согласованность BEGIN/CONFIG;
- ABORT reason, реальные частичные фрагменты, offsets и missing complement;
- известную nominal Fs partial-окна по реальным полям BEGIN;
- no-BEGIN: реальные terminal fields, BEGIN=None, `no_begin`, без массива/Fs;
- отсутствие RELEASE и правильные причины завершения.
- host_elapsed_ms соответствует первому наблюдению результата в текущей
  записи; задержка writer и предыдущая запись не сдвигают эту отметку.

Эта исходная матрица даёт 12 последовательностей и 20 завершённых архивов.
Свежие reader-процессы проверяют identity, все сохранённые поля, размеры и SHA256
members. Wire должен совпасть между compiler/Python-вариантами; число harmless
polls дискового потока или wall-clock длительность не обязаны совпадать.

## Дополнительная матрица PREPARE

В каждом варианте выполняются семь изолированных сценариев с настоящим adapter
и тем же native peer. Новый путь spool до Record не существует:

1. Отсутствующие вложенные родители действительно создаются worker mkdir;
   factory работает на том же дисковом потоке. До подготовки нет CONFIG/ARM.
   Далее настоящее Single-окно и Save.
2. Вместо родителя существует файл: prepare_failed, исходные байты сохранены,
   CONFIG/ARM, spool и архив не появляются.
3. Только для заданного mkdir моделируется PermissionError. Публичная ошибка
   prepare_failed не раскрывает внутреннюю строку; файл/запуск не создаются.
4. mkdir заблокирован gate, owner продолжает pumping через 5400 модельных мс;
   пользовательский Stop не допускает CONFIG/ARM после отпускания. Настоящие
   factory/finish/Save сохраняют пустой user_stop с stop evidence=none.
5. Retirement при ещё ожидающем PREPARE: condition lock детерминированно
   удерживает worker до disconnect/retire. mkdir/factory не исполняются.
6. Retirement внутри заблокированного mkdir: после отпускания исходный mkdir
   может создать директории, но следующая проверка retired не допускает factory.
   Нет spool, успешного Save или позднего ready_to_save.
7. Настоящий runtime disconnect внутри mkdir передаётся в observe до completion.
   После отпускания разрешён настоящий пустой recovery Save: reason=error,
   stop evidence=unknown, diagnostic=transport_recovery. Нового запуска нет.

Итого в каждом варианте десять последовательностей и восемь архивов;
четыре варианта дают 40 последовательностей и 32 fresh-process readbacks.
Четыре сценария отказа/retirement в каждом варианте намеренно не создают архив.
Wire сравнивается между вариантами для всех десяти последовательностей.
prepare.json и factory.json сохраняют заданные пути, identity потоков,
наблюдаемые границы, состояния и факт появления директорий/spool.

Выбранный spool_path не доказывает наличие файла. Создание родителей,
открытие spool, finish и Save — разные этапы. Retirement не отменяет вошедший
системный вызов; проверенные две границы не гарантируют отсутствие файловых
эффектов после последней retired-проверки или уже начавшегося factory.
Директории не откатываются. Join используется только при уборке теста,
не как обещание синхронного закрытия рабочего приложения.

## Следы и ограничения

В `receipt.json` сохраняются точные argv, версия/хеш компилятора, входы, статусы
процессов. `author-source/` содержит точные источники текущего опыта. В каждой
последовательности остаются spool, archives, ожидаемые метаданные, immutable
facts, API events, thread IDs, native trace, host/device wire. Failed output
не перезаписывается. Исходные/скопированные входы перечитываются после запуска.

Оснастка использует доверенный установленный compiler и cooperative filesystem;
это не hostile-filesystem sandbox. Реальный Qt acquisition worker и GUI в этом
опыте не запускаются: проверяется Qt-free adapter с реальным native byte peer.
Admission/Stop вызываются одним owner под общим lock; runtime pumping идёт вне
него. Диск исполняется настоящим отдельным потоком B.

Не выполняются полный firmware runtime, ADC driver, timebase, TinyUSB, MMIO,
физическая плата или USB. Fs и физический Stop не измеряются. Сэмплы имеют одно
модельное время внутри окна. Межоконные разрывы неизвестны; запись остаётся
сегментной. Полный разряд, непрерывность, Windows GUI и power-loss durability
этими результатами не подтверждаются.

Ранние авторские результаты сохранены: r1 ошибочно приравнивал config_id к
request_id CONFIG; исправлена только проверка, теперь сопоставляется CONFIG_ACK.
r2 прошёл свою фактическую матрицу, но третий Auto-результат был ABORT после
слишком раннего Stop. В следующей версии явно ожидается полный END и проверяется
точный список типов архивных entries. Это исправление оснастки и её заявленного
покрытия, не дефект B и не переименование старого результата.
