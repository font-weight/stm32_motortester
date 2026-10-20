# Original paused PR95 probe: transparent text evidence

This delivery answers [A's request](https://github.com/font-weight/stm32_motortester/pull/95#issuecomment-6122020914). It contains unchanged original source, build receipts, command logs and the four original failing-case logs in both final modes. It is a factual evidence delivery, not a new critic verdict. No original was rebuilt, executed, modified or reinterpreted. The inquiry remains PAUSED / INCOMPLETE_REVIEW_INTERRUPTED.

The full factual inventory is [published here](https://github.com/font-weight/stm32_motortester/pull/95#issuecomment-6122544290). These individual UTF-8 files are transparent project evidence, not an opaque archive. MANIFEST.json records exact original byte counts, SHA-256 and Git blob IDs. SHA256SUMS covers this delivery except itself.

## Pinned inputs and original outcomes

A source is commit b607392cb4f1cc19918e2795359df4680b645643, tree5176f9e50c2231f54c26edc9dcb13d592a04a5b7. The original selected 106-source-file manifest is copied unchanged under final-strict/source-manifest.json; the final-sanitized copy had the identical SHA-256. Every referenced A path is available under [this immutable source tree](https://github.com/font-weight/stm32_motortester/tree/b607392cb4f1cc19918e2795359df4680b645643). Exact helpers and patches remain in [tests/platform](https://github.com/font-weight/stm32_motortester/tree/b607392cb4f1cc19918e2795359df4680b645643/tests/platform) and [the USB patch directory](https://github.com/font-weight/stm32_motortester/tree/b607392cb4f1cc19918e2795359df4680b645643/firmware/third_party/usb/patches); original command arrays identify every consumed path. No firmware/protocol files are changed by this delivery.

Original strict and ASan/UBSan receipts each report30cases:26 exit0 and four exit-6. The failures are time_outputgap, time_outputgap_deadline, time_outputgap_capture and time_outputgap_capture_deadline. They fail the original abort/status-count assertion at runtime_probe.c line150. Later zero-byte/readiness assertions were not reached. This text delivery retains those failures exactly. Full receipts/command logs also preserve the healthy and precedence controls' recorded results. It does not include binaries or the original small wire files; their original hashes and availability remain in the factual inventory, and no wire bytes are fabricated.

## Critical observation limit

There is no original complete chronological trace tying every physical-register change, timebase/runtime fault latch, queue admission, DCD submission and completion together. Terminal time_fault/clock_fault values do not establish that a submission happened after a fault had already latched. These models use a native register bus and fake DCD; they are not physical TIM2/ADC/USB observations. The accepted db09/B108 observed-health contract remains a distinct result.

The original runner is supplied for inspection of its exact commands and setup, not as authorization or a request to resume the paused inquiry. This is not a self-contained executable reproducer: original source roots, patch outputs and generated artifacts remain separate, and no new run instructions or replacement oracle are introduced. A owns firmware/protocol decisions and merging.
