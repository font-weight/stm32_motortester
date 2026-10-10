# Доказательства Critic-B: TX-001

Текущий проверенный кандидат: **826bef255362b258d5dcde2100c90dc7d591edb3**,
[PR #19](https://github.com/font-weight/stm32_motortester/pull/19).
Имя `tx-06b` сохранено из исходного поручения и не обозначает текущий head.
Старый кандидат 06b используется только для воспроизведения исправленного дефекта.
Полный [отчёт и рубрика](../../WIRE-tx-review.md): component PASS 9.90;
общий контракт BLOCKED_EVIDENCE, score null.

## Состав

- `source-sha256.txt`: точные SHA-256 шести файлов PR и неизменных codec .c/.h.
- `base-to-head.patch`, `06b-to-826.patch`: полный PR diff от b6ae и delta исправления.
- `contract-b6ae-to-c54.patch`, `wire-c54.md`: точный общий draft c54 и его единственная правка.
- `github-pr19*.json`, `provenance.txt`: фактический head, API/base, сообщения PR,
  финальная сверка refs и SHA-256 полного архива.
- `run-review.sh`, `independent_tx.py`: полный независимый воспроизводитель.
  В production и авторских tests ничего не исправлялось.
- `commands.log`: фактически исполненные финальные команды, версии, хеши,
  авторские suites, независимые проверки, старый regression и host resources.
- `independent.json`, `independent-sanitized.json`: одинаковые PASS-результаты
  обычной и C ASan+UBSan сборки. 2 369 244 содержательных проверок; счётчик не
  используется как самостоятельное основание оценки качества.
- `independent.stderr`: пустой; `independent-sanitized.stderr`: только shell trace
  `cc -print-file-name=libasan.so`, без sanitizer diagnostics.
- `old-regression.stderr`: ожидаемый failure на старом 06b, exit 1;
  префикс старого Stop ACK освобождает новый барьер. На 826 тот же probe проходит.
- `host-resource.log`: только x86-64 размеры object/text и собственные stack frames.
- `native.log`, `sanitizers.log`: первичные лично исполненные авторские suites,
  до финального воспроизводимого запуска.
- `harness-development.stderr`: два исправленных дефекта только reviewer-harness:
  подсчёт старого ACK, завершённого ещё до HELLO, и обращение к tuple как object.
  Они сохранены для честной истории и не приписаны C-кандидату.

## Как повторить

Нужны Linux x86-64, GCC с ASan/UBSan, Python3, git, tar, binutils.
Проверенная среда: GCC14.2.0, Python3.12.14. Команды из корня репозитория:

```sh
git fetch origin dot-a/tx001-transport-draft dot-a/a004-session-draft
mkdir -p /tmp/tx-review-current /tmp/tx-review-old
git archive 826bef255362b258d5dcde2100c90dc7d591edb3 | tar -x -C /tmp/tx-review-current
git archive 06b67622d9ef9dbcd462478eeaa46d01dc8fe4ad | tar -x -C /tmp/tx-review-old
sh host/reviews/evidence/tx-06b/run-review.sh \
  /tmp/tx-review-current /tmp/tx-review-old /tmp/tx-review-results
cat /tmp/tx-review-results/commands.log
```

Скрипт проверяет exact input hashes, компилирует C отдельно от Python,
сохраняет все выходы в заданный results-каталог и требует ожидаемый failure
старого SHA. Финальный фактический запуск здесь использовал source-каталоги
из `/tmp/critic-b-tx-06b`; они не нужны для повторения.

Independent oracle не вызывает production encoder/decoder: Python строит
wire header, COBS и CRC через собственный код и `struct`/`zlib`, сравнивает
весь полученный поток, отдельно декодирует его и проверяет CRC.
Payload semantics и аппаратное происхождение данных не проверяются этим oracle.
ASan/UBSan применены к загруженной C-библиотеке и ко всем её вызовам harness;
Python запущен с preload ASan runtime. `detect_leaks=0`, `halt_on_error=1` для UBSan.
LeakSanitizer и полнота Python heap-проверки не заявляются.

## Граница

Доказано поведение single-owner очереди при выполнении API contract.
Fake writer моделирует необратимый упорядоченный FIFO, а не USB endpoint,
драйвер Windows или физическое устройство. Не доказаны dispatcher/generation
authority, ADC/DMA stop, настоящий capture-buffer lifecycle, USB adapter,
descriptors/VID/PID, ARM map/stack, Windows10 и уровни готовности всего MVP.
Текущий отчёт не утверждает окончательный выбор/лицензирование USB-библиотеки.
