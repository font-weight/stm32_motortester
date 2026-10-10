# Происхождение toolchain

Эта задача повторно использовала ранее проверенные официальные пакеты Debian,
локально распакованные в `/workspace/shared/arm-resource-evidence/toolchain/root/usr`.
Новых загрузок, установки системных пакетов, изменения host или платных ресурсов
в IO-001 не было. Восемь исполняемых файлов, включая gcc/cc1/as/ld, повторно
проверены по прежним SHA-256: `toolchain-binaries.json` и `toolchain-binary-check.json`.

- GCC 14.2.1, Debian package 15:14.2.rel1-1:
  https://packages.debian.org/trixie/amd64/gcc-arm-none-eabi/download
- GNU binutils 2.44, package 2.44-3+23+b1:
  https://packages.debian.org/trixie/amd64/binutils-arm-none-eabi/download
- Newlib 4.5.0.20241231-1: используются C headers, runtime не подключается:
  https://packages.debian.org/trixie/all/libnewlib-dev/download

Исходная запись проверки: `/workspace/shared/integrated-arm-budget/toolchain-provenance.md`.
SHA-256 официальных пакетов перенесены без изменения в `toolchain-package-SHA256SUMS`.
Пакеты и их extraction/bootstrap metadata остаются в `arm-resource-evidence/toolchain/`.
Эта папка не содержит копию всего toolchain; для воспроизведения нужен указанный
набор инструментов и newlib headers. Пути compiler/header задаются переменной
TC в `build_evidence.py`. Версии gcc/ld записаны заново в каждом commands.log.
