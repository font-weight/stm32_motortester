# Доставка замороженного stop r2

Эта новая Git-композиция публикует точные исходники; её SHA не равен прежнему локальному commit `a60a05c0e0b0ce9db0c907843a5be97be73e64e1`. Полное исходное дерево `7e0dda7ac222c8a5902cbb5ec9a216f6ed846b7d`, включая исходные свидетельства и неудачные прогоны, сохранено в Git bundle. Большие evidence blobs не заменены сокращёнными журналами.

MANIFEST.json задаёт порядок частей, длины и SHA-256. Каждая .b64 часть имеет собственное padding: декодировать каждую отдельно и объединить полученные bytes по порядку. Не объединять сначала строки base64. Никаких скачиваний зависимостей или запуска восстановленного кода этот шаг не требует.

В checkout с доступным original parent выполните Python3:

```python
import base64, hashlib, json
from pathlib import Path
p = Path('host/reviews/delivery/stop-r2')
m = json.loads((p / 'MANIFEST.json').read_text())
parts = []
for entry in m['chunks']:
    encoded = Path(entry['path']).read_bytes()
    assert len(encoded) == entry['encoded_bytes']
    assert hashlib.sha256(encoded).hexdigest() == entry['encoded_sha256']
    raw = base64.b64decode(encoded.strip(), validate=True)
    assert len(raw) == entry['decoded_bytes']
    assert hashlib.sha256(raw).hexdigest() == entry['decoded_sha256']
    parts.append(raw)
b = b''.join(parts)
assert len(b) == m['bundle_bytes']
assert hashlib.sha256(b).hexdigest() == m['bundle_sha256']
with Path('checkpoint.bundle').open('xb') as f:
    f.write(b)
```

Проверить `git bundle verify checkpoint.bundle`; затем `git fetch checkpoint.bundle HEAD`, `git rev-parse FETCH_HEAD` должен дать `a60a05c0e0b0ce9db0c907843a5be97be73e64e1`, а `git rev-parse 'FETCH_HEAD^{tree}'` — `7e0dda7ac222c8a5902cbb5ec9a216f6ed846b7d`. `git archive FETCH_HEAD` позволяет извлечь отдельную историческую копию, не менять текущие файлы. Родитель: `c44bae42b19ec8208c2ebee4a3e662ce1b7ff240`. Для parity сначала восстановить bundle Stop-r2: он содержит этот prerequisite.

Новый wrapper и реконструкция требуют отдельной exact remote проверки тем же Critic-B; локальные баллы не переносятся автоматически. Windows, hardware и совместная политика протокола этой поставкой не принимаются.
