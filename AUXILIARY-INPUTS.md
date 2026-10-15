# Supplemental immutable auxiliary test inputs

This data-only snapshot extends b65b3ca4773bd909b676fa5f2bd0543a319748e5.
Do not merge this tree into the application: it intentionally contains no
application or workflow implementation. The four B metadata bodies and their
PROVENANCE.md remain unchanged.

The five additional bodies below were reconstructed from the same public
run18 job115879974929 of run38572185383. They are exact hash-matching original
serializations retained in that log, not downloaded Actions artifacts and not
new Windows results. Serial/GUI JSON use timestamp-stripped original log lines
with the only hash-matching LF serialization. Recording and the two notices
use strict decoding of the original BASE64 blocks. No JSON reserialization was
used for these five published bodies. The .bin notices contain license text,
not executable native payloads.

Primary log: https://github.com/font-weight/stm32_motortester/actions/runs/38572185383/job/115879974929
B metadata handoff: https://github.com/font-weight/stm32_motortester/pull/174#issuecomment-6189690034

- frozen-recording.json: 5350 bytes; SHA256 2815dcd588a005e8a12d95283766a42db394daee6f045bc91ca831a624c24254
- frozen-serial-import.json: 413 bytes; SHA256 ab5d5bca1d75c405dc1c00ff159151f849a0c26ae01b3c5f30c0627f9e709e83
- frozen-window.png.smoke.json: 896 bytes; SHA256 1bf26ae640e0c902b7d10c61544ec72ce9f0121edfb8d1fbe0643b960793fd0c
- native-notice-source.bin: 33861 bytes; SHA256 62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120
- native-notice-copy.bin: 33861 bytes; SHA256 62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120

All nine raw metadata/receipt/notice bodies in this snapshot total551736 bytes.
Actual historical COLLECT-00.toc bytes remain missing. Its recorded SHA256 is
83a8dcdc91d8c56a15d7b42365bfbd197c7001b7c59f41fbf335d0a60133dc37.
Tests using a generated TOC must label it synthetic, update only their fixture
joins and never claim to reproduce that original TOC or a historical all-input
positive. Do not widen the fixed native99-path/123MiB or auxiliary5MiB limits.

The EXE, binary ZIP, PNG, recording archive and spool payloads are not included.
No origin authenticity, source completeness, legal/distribution clearance,
replacement result, Windows10 acceptance or physical device result is granted
by this metadata snapshot. Prior package validators and real workflow execution
remain separate mandatory evidence.
