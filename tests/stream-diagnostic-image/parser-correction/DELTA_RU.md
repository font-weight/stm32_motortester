# Исправление свидетельства PR204: lexer objdump

Исправлен дефект reviewer tools, не production. Исходные published files и ELF1480750b не переписаны. Regex4..8 hex characters мог проглотить mnemonic addcc как ещё одно поле machine bytes. В tu_fifo_correct_read_pointer он пропускал последующий uxth. Утверждение о полном instruction walk требовало исправления.

В отдельной копии trace_callbacks.py/check_sp.py принимают только слова ровно4 либо8 hex characters. Replay на неизменном ELF1480750b дал9→10 visited instructions для tu_fifo_correct_read_pointer (entry0x08002a5c). Все call edges, frames/returns и полный static-bound.json идентичны: foreground2792, USB264, total3164, aligned3168 B. Numeric impact отсутствует; это установлено повторным расчётом, не предположено по размеру правки. Нельзя переносить число на другой ELF.

Рецепт:
python3 -I -B reproduce_parser_delta.py --proof /путь/к/исходному/portable-proof

Рецепт сверяет original manifest, меняет только рабочие копии2 scripts, повторяет trace/SP/bound, сравнивает edges/frames/full bound JSON и проверяет неизменность исходного пакета. Логи и parser-delta.json сохраняются в отдельном runs/<id>.5 запрещённых Python modes также реально отвергнуты guard с exit2. Это не новое аппаратное/timing/flash одобрение. Новые instrumented proofs используют исправленный lexer и явную regression-проверку addcc2B+следующей uxth.
