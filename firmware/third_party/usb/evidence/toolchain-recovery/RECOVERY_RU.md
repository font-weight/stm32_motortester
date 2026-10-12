# Восстановление того же ARM toolchain

10.10.2026. На основании отдельного разрешения восстановлены три ранее использованных официальных Debian-пакета. Source metadata прочитаны из PR39, точный head aad61d598aa9996b1cd2c0587e936e19118fc087; Git blob SHA текстовых provenance-файлов проверены.

Официальные источники и SHA-256 повторно прочитаны:
- https://packages.debian.org/trixie/amd64/gcc-arm-none-eabi/download
- https://packages.debian.org/en/trixie/amd64/binutils-arm-none-eabi/download
- https://packages.debian.org/trixie/all/libnewlib-dev/download

Файлы получены по HTTPS с ftp.debian.org из указанных официальными страницами pool-путей; сохранены GCC/binutils download logs. Точные URLs и ожидаемые hashes — package-download-manifest.json. Все package hashes совпали до распаковки: package-check-new.log. Затем выполнен только dpkg-deb --extract в /workspace/shared/arm-resource-evidence/toolchain/root; dpkg install, apt и maintainer scripts не запускались. Полный extraction log сохранён.

После распаковки восемь compiler/binutils исполняемых файлов сверены с hashes PR39: toolchain-binary-check-new.json. Только после успеха запускались gcc/as/nm/size/readelf для свежих USB objects. Firmware и импортированные C-модули не исполнялись. ld не использовался для link.

Пакеты и установленная в локальный каталог копия toolchain находятся вне vendor и вне небольшого USB-архива. Это восстановление прежнего официального набора, не выбор новой версии.
