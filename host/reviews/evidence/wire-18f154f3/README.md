# Независимые reviewer-проверки byte-codec

Авторы codec в этих скриптах не участвовали. Это свидетельства Critic-B, не production-код и не готовая проверка общего протокола. Скрипты сохранены отдельно для воспроизведения командой A.

Проверенные commits:
- `18f154f3e864dc30ab8d56fd78c1266023df883a`: `check-results.json`.
- `a81339d087fac4d77017a22dcce1f4fe11cfc46b`: `check-results-a81339d.json`. Новый commit извлечён и перекомпилирован отдельно; те же проверки повторены, включая ASan/UBSan.

Среда: Linux x86_64, GCC 14.2.0, Python 3.12.14. Для обоих SHA ASan/UBSan завершились с exit 0 после 200000 итераций random decode и encode/decode. LeakSanitizer не проверен: несовместимость среды с ptrace, используется `detect_leaks=0`. Никаких свидетельств Windows, USB, потокового накопителя или аппаратуры здесь нет.

Запуск из корня checkout:

```sh
SHA=18f154f3e864dc30ab8d56fd78c1266023df883a
T=$(mktemp -d)
for P in firmware/protocol/mt_wire.h firmware/protocol/mt_wire.c protocol/vectors-v0.1.json; do
  git show "$SHA:$P" > "$T/$(basename "$P")"
done
cp host/reviews/evidence/wire-18f154f3/check_wire.py "$T/"
cp host/reviews/evidence/wire-18f154f3/sanitize_wire.c "$T/"
gcc -std=c11 -O2 -Wall -Wextra -Werror -Wpedantic -Wconversion -Wsign-conversion -fPIC -shared "$T/mt_wire.c" -o "$T/libmt_wire.so"
python "$T/check_wire.py"
gcc -std=c11 -g -O1 -Wall -Wextra -Werror -Wpedantic -Wconversion -Wsign-conversion -fsanitize=address,undefined -fno-omit-frame-pointer "$T/mt_wire.c" "$T/sanitize_wire.c" -o "$T/sanitize_wire"
ASAN_OPTIONS=detect_leaks=0 "$T/sanitize_wire"
```

Для второго прогона заменить SHA на точно указанный второй commit. Вывод PASS означает только прохождение проверок скрипта; формальная приёмка описана в независимых отчётах и остаётся BLOCKED_EVIDENCE/score:null.
