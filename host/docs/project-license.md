# MIT для собственного кода Windows-приложения

Владелец выбрал MIT для собственного кода Windows-приложения 13.10.2026.
Полный английский текст находится в [host/LICENSE](../LICENSE).
Настоящий документ определяет область этого текста: наличие `host/LICENSE`
не означает, что каждый файл внутри `host/` или весь репозиторий имеет MIT.

## Область предоставления

MIT предоставляется в пределах прав владельца на оригинальный код приложения
и его сопровождающую документацию в следующих местах:

- `host/motortester/*.py` — приложение, ядро, транспорт и эмуляторы;
- `host/scripts/*.py` — вспомогательные сценарии приложения;
- `host/packaging/*.py` и `host/packaging/motortester.spec` — собственный код
  сборки, проверок и инспектора;
- `host/tests/*.py` — собственный код тестов и их поддержки;
- `host/README.md` и `host/docs/*.md` — собственный текст документации приложения.

Здесь `*` обозначает файлы непосредственно в указанном каталоге.
Исключения ниже действуют также внутри перечисленных файлов: копирование чужого
материала, импорт библиотеки или помещение её в сборку не переводит её под MIT.
Материалам за пределами этого перечня настоящим решением лицензия не назначается.

В уведомлении используется `Copyright (c) 2026 font-weight`.
`font-weight` — имя владельца [репозитория](https://github.com/font-weight/stm32_motortester)
и автора [первого host-коммита от 10.10.2026](https://github.com/font-weight/stm32_motortester/commit/7d74cdf1cb04d8d3cbd9edcd06d6b23a27007392).
Отсюда взяты обозначение и год; полное юридическое имя не добавляется.
Это уведомление относится к собственным материалам владельца и не заменяет
уведомления других правообладателей.

## Что исключено

- Все сторонние исходники, бинарники, данные, шрифты, изображения, фрагменты
  кода и текста сохраняют собственные условия независимо от их расположения.
  Это включает CPython и его стандартную библиотеку, PySide6/shiboken6/Qt,
  плагины Qt и их зависимости, pyqtgraph, NumPy и включённые в него библиотеки,
  colorama, pyserial, PyInstaller/bootloader/hooks и остальные зависимости
  сборки и выполнения. OpenSSL, Mesa и компоненты Microsoft также не получают
  MIT проекта при попадании в пакет.
- `host/packaging/licenses/**`, скопированные при сборке license/copyright/notice
  и `licenses/CPython-LICENSE.txt` в пакете остаются текстами соответствующих
  правообладателей. В частности, сохранённые GNU-тексты Qt и уведомление
  pyserial нельзя заменять этой MIT-лицензией.
- `host/tests/fixtures/**` целиком исключён из этого предоставления, в том числе
  копии `device_emulator/source/wire-v0.1.md` и `session-guard.md`, эталонные
  векторы, двоичные данные и сохранённые журналы. Исходные права и условия
  скопированных материалов сохраняются; отсутствие отдельной лицензии
  не превращает их автоматически в MIT.
- `host/evidence/**`, `host/reviews/**`, исторические manifests/JSON, записи
  измерений и прочие выходные артефакты не получают MIT только из-за своего
  расположения или создания программой. Собственный код, включённый в бинарную
  сборку, сохраняет MIT в указанной области; весь EXE/ZIP единым MIT-пакетом
  не объявляется.
- Всё за пределами `host/`, включая `firmware/`, общие `protocol/`, корневые
  документы, аппаратные материалы и vendor-компоненты, находится вне этого
  решения. Оно не назначает и не меняет лицензию прошивки или репозитория целиком.

## Передача исходников и готового пакета

Для копий или существенных частей охваченного MIT кода требуется сохранить
copyright и permission notice из `host/LICENSE`. Документ об области следует
передавать вместе с ним, чтобы исключения оставались понятными. Это пояснение
области, а не дополнительные условия поверх стандартного текста MIT.
Сторонние уведомления и остальные применимые требования сохраняются отдельно.

Выбор MIT уточняет только прежний вопрос о лицензии собственного приложения
из пункта 1 [LICENSE-REVIEW.md](../packaging/LICENSE-REVIEW.md).
Исторические документы и доказательства не переписываются. Полнота сторонних
уведомлений, точные corresponding source и доступ к ним, замена библиотек,
условия CPython/Qt/PyInstaller и других компонентов, проверка Windows-пакета
и разрешение выпуска остаются отдельными незакрытыми проверками.
Этот документ не является юридическим заключением или приёмкой распространения.

Ниже помещён полный текст, побайтно совпадающий с `host/LICENSE`, чтобы
документация могла нести уведомление вместе с областью его действия.
Текущий `motortester.spec` включает `host/docs`; рецепт `build_windows.py`
не копирует отдельный `host/LICENSE` в бинарный пакет. Его `source.zip`
архивирует `host/` из выбранного Git-коммита. Для нового коммита всё равно
нужно проверить фактическое наличие обоих файлов в source-архиве, а в готовом
ZIP — наличие и читаемость этого документа с полным MIT-текстом.
Это ещё не выполненная проверка нового ZIP. Рецепт, зависимости и ранее
собранные артефакты этим изменением не меняются.

Текст MIT сверен 13.10.2026 с [Open Source Initiative](https://opensource.org/license/mit)
и [SPDX MIT](https://spdx.org/licenses/MIT.html). Сохранены все три стандартных
абзаца: разрешение, сохранение уведомления и отказ от гарантий/ответственности;
подставлены только год и обозначение правообладателя.

## Полный текст MIT для указанной выше области

```text
MIT License

Copyright (c) 2026 font-weight

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
