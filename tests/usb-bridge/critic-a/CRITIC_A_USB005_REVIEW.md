# Critic-A: USB005 guarded real-core integration, local source review

Repository: `font-weight/stm32_motortester`. Task: [issue 60](https://github.com/font-weight/stm32_motortester/issues/60), including the explicit adapter/cleanup scope clarification [6099776688](https://github.com/font-weight/stm32_motortester/issues/60#issuecomment-6099776688). Date: 2026-10-10 UTC. Critic-A is independent of the production author and made no production changes.

## Result and version

No remaining demonstrated P0/P1 or substantive in-scope implementation defect was found. The local mandatory source/native/ARM-object checks below pass. The P2 ARM runner output-isolation defect is corrected and independently reproduced. **Local verdict: BLOCKED_EVIDENCE; score: null pending final package review and immutable publication binding.** This is not a claim that unrelated board tests are mandatory for this deliberately bounded source/model/object task.

Planned sole parent: USB004 PR61 `7a721f82b98676d6fd9ab1b58a1d9b7d617b47a5`, tree `0d3f1acec1750cc0c3c9102d56fa211ce7729ead`. I independently read back its commit/tree identity. The final additive package and its two existing adapter replacements require a separate tree/content check. No final candidate Git SHA exists in this report. The pristine vendor remains an input; patched outputs are generated in separate build directories.

Reviewed and personally tested SHA-256 values:

| Input | SHA-256 |
|---|---|
| `mt_usb_bridge.c` | `7cbbc13e481aa398b28f713f2b3067f27e622c99cd8c1eed833237b04cf59d7e` |
| `mt_usb_bridge.h` | `5894b781971f5e5b3179f612d909f4299905fb997132008aa33225e9f2e35866` |
| `mt_usb_cdc.c` | `d262e01072dca405be71af32db4263373c100eb9e618959f1be25e1b7ca3cf9f` |
| `mt_usb_cdc.h` | `4f7d054f5ec14c4b04b6b28240c93af2e9894e16c72db7a369f2d3221800563b` |
| generated `usbd.c` | `f500d3477ef628c15e8ebb117570da64d34d4b6d638cfa47886422b9a04091fe` |
| generated `dcd_stm32_fsdev.c` | `d46344c2dea3c7eceaa4a1042bd179ef3eda0e835efdf3ae3763f18db44961d8` |
| root `tests/usb-bridge/README.md` | `03ef3a66a04230e66890ecff65b054feeb28904476fee98da4258abfff6d5e2f` |

The USB005 patch manifest pins USB003 core input `187144971b52e77385809500bd7401b46c3c332acda5f6221a1b45e440b8ddd0` and USB004 DCD input `1326ac072ff0f687f6589e5045184c77d578687f13e58b85b1e783aa2946fe97`. The pinned TinyUSB origin is commit `dae3f9a366bfcddbf9dcf1b48d7500286a849539`.

## Accepted review boundary

Issue 60 defines an opt-in integration with the real pinned TinyUSB core/CDC/FIFO, private queue envelopes, USB002 metadata, preserved operation actors and explicit DCD guards. The configuration is STM32F103xB, one CDC, dedicated HWFIFO, OS_NONE, queue16, one event per task and a single foreground owner. Native evidence uses a modeled interrupt mask and fake DCD. ARM evidence compiles the actual complete patched core and FSDEV translation units. It does not execute ARM or link firmware.

The production owner must provide nesting-safe save/restore callbacks which preserve the actual previous interrupt mask and serialize all metadata access and the whole DCD operation. USB HP/LP producers must not nest. `cleaned(E)` is only the caller's receipt after a foreground cleanup attempt. It neither performs Stop nor proves its success. Init/reinit and deinit are separate serialized lifecycle phases; reinit with live events/operations is forbidden.

Config0 deliberately poisons the epoch before unsupported teardown and requires a fresh real reset. This is a disclosed limitation of the candidate, not a universal USB-host requirement. Configured receipt means successful status submission, not reception of status ACK. An inherited actor remains old after ingress reset; nested CDC operations cannot acquire the new epoch while an old callback is active.

## Personal source review

I read the bridge, adapter, complete patch deltas, affected genuine core paths and FSDEV address path independently before relying on the supporting outcomes. Key checks:

- Accepted SETUP increments under the queue lock; every popped SETUP, including stale work, decrements once. The held envelope preserves its original token and queue order. Full queue loss poisons the current epoch; a lost reset cannot be replaced by a fabricated receipt.
- Dispatch has a separate body so upstream early returns do not leak an actor. A recursive task does not pop another event during an active actor. Producer ingress never replaces that actor.
- Each of the nine explicit guards checks admission while locked and holds the same guard through the complete raw DCD call and result accounting. Same-translation-unit weak functions are not assumed to obey linker `--wrap`.
- False transfer/FIFO/open and missing/false hidden-address receipt poison the current actor once. A denied old-actor operation does not blame the fresh epoch. Failed completion is latched at ingress, before a callback can submit more work.
- Mount alone does not permit application I/O. Endpoint/status failures do not create configured receipt. Unsupported config0 cannot execute teardown first and then declare failure.
- Public adapter open/write/step establish or inherit a scope. The step's internal writer executes within its enclosing scope. Positive FIFO-copy counts remain accepted prefixes even if subsequent submission fails. Same-epoch sticky faults may quarantine an already-open adapter; diagnostics and failed Stop ownership remain in the controller.
- The reviewed HWFIFO configuration avoids claiming buffered non-F1 soft-pause data retention. Core/DCD opt-out equality and the adapter's deliberate opt-out hard-loss difference are distinguished.

## Executable evidence personally reproduced

Environment: Python 3.12.14, native GCC 14.2.0, ARM GCC 14.2.1; strict project C11 with `-pedantic-errors -Wall -Wextra -Werror`, upstream GNU11 with its stated warnings. Native sanitizer runs use ASan+UBSan, halt on error; LSan disabled because this environment does not support its ptrace-dependent operation. No LSan pass is claimed.

### Unchanged supporting suites

I copied the frozen r2 harness and production inputs, then personally ran all six suites using runner `1cd9834901f15af1226c2a9d79c240a7b6316d82f006d6eb36eba1ac5077d2fb`:

- 47 shim scenarios, strict and ASan+UBSan.
- 40 genuine-core scenarios, strict and ASan+UBSan.
- 45 genuine-core plus actual adapter/controller scenarios, strict and ASan+UBSan. These repeat the 40 core cases and add five adapter cases; they are not 45 additional independent scenarios.
- All 12 native runner tooling regressions passed.

Every receipt reports unchanged input hashes before/after execution. The assertions, not just printed PASS labels, are the oracle. Coverage includes reset before each guard, pending reset deferred through each whole call, previous mask0/1 restoration, nested inheritance, missing guard pairs, queue/reset loss, stale SETUP/deferred work, callback reset, suppressed stale ZLP/rearm/stall/status, config failures, soft-deferred HWFIFO bytes, exact positive FIFO-copy count and actual adapter recovery with fake failed Stop.

The supporting fake DCD is generated by disclosed instrumentation of the earlier immutable fake. Its SET_ADDRESS uses the exact complete patched production function body plus a call counter. This is not execution of complete MMIO DCD. The generic integration pump substitutes `cleaned(current)`; only the dedicated adapter cases run the actual controller recovery path with a fake Stop callback. No complete owner-pump proof follows.

Personal receipts: `rerun-{shims,core,adapter}-{native,sanitized}/receipt.json`; native tooling `rerun-tooling/tooling-regressions.json` in the review evidence. The package may preserve selected receipts rather than generated binaries or duplicate snapshots.

### Supplemental Critic-A assertions

`test_extra.c` and `run_extra.py` passed 10 cases under strict/native and ASan+UBSan. They reuse the disclosed supporting fixture/fake model; they are not a second independent peripheral implementation.

1. A held older SET_CONFIGURATION(0) is superseded by a newer accepted config1 after producer-side resume; it does not spuriously poison the epoch.
2. Respectively 1, 255, 256, 257, 511, 512, 513 and 1025 rejected SETUP events preserve exact queue-loss diagnostics and permit configuration only after a fresh reset, with no phantom counter wrap.
3. A failed old FIFO submission plus a reset pending inside its guard preserves the failure diagnostic but does not carry the old sticky fault into the fresh epoch; copied64/host0 remain distinct.

Portable command after a fresh supporting core run:

```sh
python3 path/to/critic/run_extra.py --base-run /fresh/core-run --out /fresh/extra-run
```

The runner recompiles the exact receipt commands, substituting only the supplemental test source, and verifies input preservation. Repeat with the sanitized core receipt for sanitized checks. Logs/receipts: `extra-native-final.log`, `extra-sanitized.log`, and the corresponding saved receipt files.

### Exact patch-tool behavior

`test_patch_tool.py` personally passed 28 applicator invocations, 14 per core/FSDEV patch. Tests cover correct exact output, existing output/source alias, nested source/patch output, symlink parent, existing/dangling symlink, existing directory, incorrect source/patch/result hash, a concurrent exclusive-output creation and missing output parent. Mutations affect disposable copies; original inputs remain unchanged. GNU patch is invoked with zero fuzz and the exact result hash is checked. These are tooling checks, not lifecycle evidence.

### Actual ARM compilation and disassembly

I personally ran corrected ARM runner `e04248ce32792c6be88280cfdbe9ea463870edaba86897675a1da6e18b6c7f95` and its audit, rebuilding all ten variants. All ten object hashes exactly match supporting run-03. Complete enabled object hashes:

- bridge: `898746106678d1a020d9b443fb63b9562ee11f0be15e254a6ab24de002ce67f4`
- core: `d95561051ae31a69fef406c3286186663a5d8ae6da7ba0ac514d3a6263f9c8f7`
- DCD: `e17309dc7228f94f02a2f9a934dc76f4c71015abb9715d0cee5d45be3a333bf0`
- adapter: `ff73d60b3a272f133341ab9f814554593863af5c2054ee7d0157d839dd172d49`

| Object | text/rodata | data | bss | Maximum local frame |
|---|---:|---:|---:|---:|
| bridge |1294|0|72|40|
| core |4216|29|391|96|
| FSDEV |2808|0|131|72|
| adapter |778|0|0|40|

Four-object sums are 9096 text/rodata, 29 data, 594 bss. These omit epoch, CDC/FIFO, owner/startup/runtime and the rest of firmware. Section sums and local frames are not a linked Flash/RAM budget or maximum call chain. All three DCD variants retain the same five upstream conversion warnings; bridge/core/adapter have none. Werror is not falsely claimed for the complete ARM DCD unit.

The opt-out core and DCD are byte-identical to their prior baseline after preprocessing and as objects. Adapter opt-out differs only by the disclosed same-epoch hard-loss condition, with text604→588. The enabled core has no direct call/jump relocations to the nine guarded DCD targets; explicit shim callsites are present, including weak-target paths. I inspected the guard branching and hidden-address sequence; relocation presence alone is not a control-flow proof. FSDEV's pinned transfer function always returns true, so its compiled address receipt is constant true after the raw call. This verifies wiring only, not detection of physical submission failure or host ACK.

## Findings and dispositions

### CA-USB005-1, P2, corrected: ARM output isolation

The initial ARM runner `2c964466cc25caaeee7775a5e75065ee87f6ed3eae8f189cea75c4c006daf6da` created output before validating source separation. A nested output could recursively copy its own snapshot and write into an input tree. My disposable-input reproducer showed 12 nested/symlink destinations changed before failure; six existing-directory cases rejected without writing. Original runner and receipt are preserved, not rewritten as a pass.

The corrected runner validates actual paths, required inputs, copied subtrees and singleton aliases before mkdir. I reviewed the exact delta, reran the original 18-case reproducer (all rejected without writes), and independently ran all 61 complete-fixture positive/negative path cases (all pass, all fixtures unchanged). The latter includes real valid-fixture overlap cases; the simpler 18-case missing-input reproduction alone would not prove the full fix. I then rebuilt/audited all ten ARM objects unchanged. The package's documented removal of three unused exploratory snapshot inputs requires a separate packaging-delta check, rather than silently treating its runner hash as the same file.

### CA-USB005-2, API scope clarification, closed for this bounded review

Source inspection found raw lifecycle/remote-wakeup/ISO entry points outside the nine guards. Root explicitly documented the unsupported runtime APIs: `tud_remote_wakeup`, connect/disconnect/configure, SOF enable and ISO allocation/activation. Init/deinit have separate serialized lifecycle rules; test mode and custom class/SOF/xfer-ISR callbacks are excluded. `app_enter` is not a blanket safety permit for every public TinyUSB API. The ARM audit confirms the excluded symbols still exist; no claim that the implementation guarded or removed them is made.

The existing adapter replacements and the cleanup-receipt/failed-Stop distinction were also explicitly added to issue60/header documentation before freeze. No host/wire scope was changed.

## Rubric and remaining binding

Under quality-gate revision1.0, C1 correctness and C2 failure handling are supported by the source mapping and assertion-backed normal/fault/queue/reset tests; C3 by personal reruns and preserved negative evidence; C4 by the narrow opt-in patch/API boundaries; C5 by hash-pinned inputs, portable applicator and traceable runners. A numeric aggregate is withheld here until the final package is independently reproduced and bound to an immutable exact head/base/tree. No score is inferred from test counts, another worker's success statement or ARM compilation alone.

Open outside this scoped software/model/object result: actual PRIMASK implementation; IRQ vectors/priorities/PRIGROUP and latency; complete foreground owner pump; failed-Stop/time/ADC ARM commit gate; PMAOVR; real enumeration/ACK/reset/disconnect and old-transfer cancellation; linked firmware memory/stack; Windows/device connection; low-voltage and ignition validation. This review authorizes no merge, release, shared lifecycle-policy adoption or hardware operation.
