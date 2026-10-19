# Отдельный диагностический STREAM boot

Этот draft использует только mt_live_runtime. Старый mt_device_main и исторические
рецепты/board ABI не меняются. Новый entry нельзя включать одновременно со старым.
Boot сохраняет reset/clock/TIME/USB порядок и допускает IRQ только после повторной
проверки stopped/unowned ADC/DMA/TIM3, gate/reset/clock и USB NVIC состояния.
Main вызывает только mt_live_runtime_step. При TIME_FAULT/RECOVERY storage живёт
дальше; повторные Stop выполняет runtime. Это не физическая проверка устройства.

mt_stream_timing — отдельный const объект из трёх uint32, schema=1,
diagnostic_provisional=1, явно заданный declared_transaction_bound_ms в 1..499.
Никакого значения по умолчанию или режима «qualified» нет. Имя параметра runtime
не превращает provisional declaration в подтверждённую временную границу.

build/stream_timing.py проверяет JSON синтаксис и создаёт C с exclusive-create.
Точные поля: schema_version, input_kind=diagnostic_provisional,
profile=stream-only-v1, source_commit (40 lowercase hex),
declared_transaction_bound_ms (целое 1..499). Boolean вместо числа, дроби,
NaN/Infinity, дубликаты и лишние/отсутствующие поля запрещены.
Standalone parser проверяет синтаксис source_commit, а не наличие/содержимое Git.
Внешний рецепт обязан сверить его с закреплённым source snapshot, проверить
canonical paths, входные/сгенерированные hash и отсутствие input drift.
Успех генерации не является разрешением прошивки или подтверждением bound.

Отдельный recipe уже линкует диагностический ELF с generic BOOT/layout checks.
Описание — в ../build/STREAM_DRAFT_README.md. Полный callback/stack proof ещё открыт.
Прежние stack bounds/образы не переносятся на новый профиль. BIN/HEX не выдаётся.
Аппаратные проверки, квалификация transaction bound и firmware profile открыты.
