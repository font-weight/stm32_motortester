# Происхождение и границы доказательств

Source snapshot: `8c51c78024a2e1bcd1e5575ef9189737a3ddce88`.
Полный compile/include/patch input manifest — `source-manifest.json`.
New module SHA-256:

- mt_stream_diag.c: `8ead5517425c540a7876eaa540edca7205af5c904970e5688aec17b1718251c3`
- mt_stream_diag.h: `a2dc9c01d5a1ca9168e776e33c719ec727f0e0e301aad755d12b3280931774e5`

## Fixture lineage

`test_runtime.c` развивается из независимого 47-case STREAM runtime package,
ранее byte-pinned к cc2c938b122cc580d959740e16d11a2ac046b265. Здесь production
inputs перепривязаны к 8c51c780 целиком; добавлены 17 diagnostic cases, отдельная
DWT модель и наблюдающие wrappers реальных функций. Fake-DCD generator взят из
текущего pinned repository. Старые fixture actors/ADC/TIME/USB checks сохранены.

`test_boot.c` развивается из 86-case boot fixture, ранее привязанной к
3caaa9106a3b6356e471c5e03ae0594e20472f7a. Здесь она проверяет actual enabled
boot source 8c51c780 и четыре дополнительных DWT failure cases. Внедрения
ошибок производятся в MMIO или explicit test-model public fields; production
return values не заменяются.

`test_diag_unit.c` создан независимо для этой задачи. Он компилируется
отдельно от mt_stream_diag.c. Исходный unit fixture имел SHA-256
`ff15963ef950ef153d4098880597f99ee9b25178d57f04cdc9483c603a9cb284`;
при интеграции изменена только строка PASS output на structured JSON.
В portable включён C fixture, а не временный worker runner/logs/binaries.

`run.py`, `run_boot.py`, `check_runner.py` основаны на предшествующих внешних
native runners. Все source pins и generated objects создаются только в fresh
external output. Source/source-stage hashes проверяются после выполнения.
`PACKAGE.json` перечисляет portable files с hashes; он не копирует production.

## Наблюдавшиеся ранние отказы

Первый runtime запуск отказал **до создания output** из-за изменения pinned
README во время движения production worktree. После сообщения root snapshot
перепривязан к неизменяемому 28dab682.

Первый enabled boot fixture неверно требовал zero DWT activity для любого
stage3 отказа, включая повторный entry после уже успешного runtime init.
Это fixture defect: assertion уточнён для ранних отказов. Исходный failed
boot-r1 receipt/log остаётся во внешней evidence папке; boot-r2/final прошли.

Первый отдельный unit LeakSanitizer attempt заблокирован ptrace environment.
Успешный ASan/UBSan rerun использовал detect_leaks=0. Исходный failed log сохранён
в unit-work, а не скопирован в portable и не заявлен проходным.

## Доставка доказательств

`RESULTS.json` содержит только итоговые counts/limits и SHA-256 внешних receipts.
Full commands, logs, binaries, patched/staged source и generated declarations
находятся только в OUTPUT, создаваемом при replay. Они **не bundled**.

Default-off object comparison выполнен отдельным image-recipe auditor;
default-off full ELF replay выполнен root на d5d43d42d804d0488d496e4c458fc858ba68af7e.
В `DEFAULT_OFF_EVIDENCE.json` сохранены только attribution, receipt hashes,
input/object/ELF hashes. Это не наш самостоятельный ARM replay и не доказательство
instrumented image. Более ранний enabled ELF специально не используется.

Никаких аппаратных результатов, физической timing qualification, stack proof,
прав на USB identity или flash approval этот пакет не создаёт.


## Исправление DIAG-START-001

Ревью исходного 28dab682/e533e53 выявило пропущенную первую валидную попытку
START при отказе preliminary quiesce до live_start. Исходный вердикт REWORK
сохранён отдельно. Исправление 8c51c780 переносит BEGIN перед этим барьером.
Два новых случая увеличивают runtime inventory с 62 до 64. Новый тест
отвергает прежний runtime в strict и ASan/UBSan; исторические результаты и
DEFAULT_OFF_EVIDENCE относятся только к указанным в них ранним source pins.
