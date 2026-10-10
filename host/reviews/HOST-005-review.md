# Critic-B: HOST-005, актуальное заключение 4e8d0a34

Дата: 10.10.2026. [Issue #22](https://github.com/font-weight/stm32_motortester/issues/22),
[PR #26](https://github.com/font-weight/stm32_motortester/pull/26).

**PASS, 10,0/10 для заранее объявленной синтетической интеграции HOST-005.**
D-005-01 закрыто на новом exact SHA. Незакрытых замечаний этого scope не осталось.
Общий wire/session/USB, Windows и software-ready этим заключением не принимаются.

## Версия и сохранение предыдущего результата

- Новый head: **`4e8d0a3497ee2a8203d2f3e97d8c1293e92dade4`**, подтверждён GitHub API;
  проверен собственный чистый `git archive`.
- Parent: `cd86784b434662c7aa542713fa61a5bbd6b97938`; PR base snapshot при чтении —
  `cb271a13b73db3ad480afe654e9efe3f97c7eb41`.
- Предыдущий PASS 9,90 сохранён побайтно:
  [HOST-005-review-original.md](evidence/host-demo-cd867/HOST-005-review-original.md),
  SHA-256 `9546ea9c8698739e6d19563cd3adcff4a608a3eaac6c26bc525c314a8f0628c4`.
  Это архивный отчёт, его исходные относительные ссылки были рассчитаны от host/reviews.
- Независимый Critic-B, gpt-6-astra xhigh; не автор production/test/docs изменения.
  Рубрика v1.0, исходные требования и интеграционная base correction из #22
  не менялись. Числовой результат не перенесён автоматически.

## Полностью проверенный delta

Два файла, 24 добавления/9 удалений:
`host/docs/wire-demo.md` и `host/tests/test_wire_demo.py`.
Все **10 production Python-файлов побайтно идентичны** cd86784b.
В частности, SHA-256 wire_demo.py:
`c79f83b68703477c46dc5c6881091b373b6280a4204d5f26ac2c4f0adb0f48ce`.
Восстановленные строгий UTF-8/безопасный русский overwrite и window helper
не изменены новым commit; результаты их предыдущей exact-composition проверки
остаются применимыми после проверки diff и полного текущего suite.

**D-005-01 закрыто.** Документ теперь точно говорит о девяти корректных
последовательных DATA-блоках, разрешает размеры, отличные от генераторных,
и приводит 249×8+56. Новый product regression собирает такое окно с правильными
sequence/offset/CRC и проверяет полное совпадение кодов. Косметические пробелы
не изменяют смысл остальных ограничений. Это исправление документального
описания уже правильного поведения; функциональный red/green тут не заявляется.

## Лично выполненные проверки на новом head

Среда: Linux x86-64, Python 3.12.14, прежние pinned dependencies, Qt/offscreen.
Чистый архив: `/tmp/critic-b-host-demo-4e8d/source`.

1. **209 полных тестов, 16,201 с, OK**.
2. Все **8 feature-тестов под `python -O`, 1,393 с, OK**. Полный 209-test optimized
   прогон на этом head не заявляется; полный 208-test optimized прогон выполнен
   на предыдущем head, production и старые тесты здесь неизменны.
3. Собственный независимый delta harness проверил четыре корректных разбиения
   на 9 DATA — 249×8+56, 248×8+64, 250×8+48, 250×7+149+149. Все приняты,
   2048 кодов сохранены точно. Полные корректные массивы в 10 DATA (225×9+23)
   и11DATA (200×10+48) отвергнуты, как обещает новая документация.
4. Прочитан весь diff и связанный раздел API; новые зависимости/side effects
   отсутствуют. Документ и product regression подтверждают одну и ту же политику.

Команды из exact archive:

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  XDG_CACHE_HOME=/tmp/critic-b-host-demo-4e8d/cache \
  PYTHONPATH=/tmp/motortester-deps:$PWD/host \
  python -m unittest discover -s host/tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host \
  python -O -m unittest discover -s host/tests -p test_wire_demo.py -v
```

Новые [логи/harness/result](evidence/host-demo-cd867/delta-4e8d/).
Для повторения независимого probe:
`python check_layout_delta.py /path/to/exact/source` без `-O`.

## Применимость прежних доказательств

Полная [история и исходные артефакты](evidence/host-demo-cd867/README.md)
сохраняются: реальный 20-window CLI с 1-byte chunks; 260 positive сочетаний,
341 отвергнутый поток без публикации, exact metadata, bounded-memory early
rejection, no-clobber/atomic failure и честная partial-batch политика;
полная сверка 20 MTCAP/CSV/SVG и визуальный просмотр настоящего SVG.
Эти проверки не повторены искусственно как якобы новые измерения: их
production-зависимости byte-identical, а текущая композиция дополнительно
прошла полный suite и адресные новые проверки.

## Фиксированная рубрика

| Критерий | Вес | Балл | Основание |
|---|---:|---:|---|
| C1 | 30% | 10 | Предзаданный синтетический путь, коды/метаданные и теперь точное описание layout подтверждены |
| C2 | 20% | 10 | Fail-closed/границы/файловые отказы сохраняются в неизменном production и полном suite |
| C3 | 25% | 10 | Exact-SHA 209 tests, optimized feature suite, независимые positive/negative layout probes и проверенная применимость прежних доказательств |
| C4 | 15% | 10 | Узкий, явно synthetic bridge; scope/нет USB или handshake/partial batch описаны честно |
| C5 | 10% | 10 | Единственное документальное замечание исправлено, команды/источники/артефакты воспроизводимы |

Итог:
`0,30×10 + 0,20×10 + 0,25×10 + 0,15×10 + 0,10×10 = 10,0`.
Все обязательные свидетельства ограниченной задачи имеются, P0/P1 отсутствуют.
Изменение C5 связано с проверенным исправлением и новым regression, не с числом
прогонов или пожеланием повысить балл.

Этот результат относится только к 4e8d0a34. Будущая композиция с PNG/новой базой
требует своего exact-SHA review. Общие TX/Stop/session, принятый контракт,
реальный USB, Windows build и аппаратные доказательства остаются отдельными gates.
