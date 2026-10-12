# Critic-A: USB-001 foreground CDC adapter

Date: 2026-10-10. Scope: [issue46](https://github.com/font-weight/stm32_motortester/issues/46), the bounded foreground adapter on PR44 and the honesty of its software-model evidence.

**Verdict: BLOCKED_EVIDENCE; score null.** No remaining in-contract implementation defect was demonstrated in this candidate after the early review refinements. The executable checks below pass. Production lifecycle/IRQ ingress, epoch barriers, time-health integration and hardware evidence are absent. They are not inferred from a manually supplied stable predicate, a fake DCD or an ARM object. An exact publication binding is still required.

## Reviewed identities

- `mt_usb_cdc.c`: SHA-256 `4127541694a00521f0eddce314bbce8383da892b2a5adbb0e5bace6576d0193f`.
- `mt_usb_cdc.h`: `9ab7a12dcfb1d0d3282d151f0c07884d8b30fcee3cd4e4ef893b6bc13a78f7b4`.
- Final Russian `USB_CDC_README.md`: `648129e5b403840f0a17ade59e605422d50dd8c242a7cf4a03d117af16b126b2`.
- Supporting adapter test: `30d45f6160a7f90d8db7f079417cc079da2ca784dff2d434b30af75a75fca16e`; fake DCD: `fa91bed60b415d7c09e1b4aa1c3865f127d29232113c7dc1e1344e8469c595da`.
- Frozen supporting archive: `77ae30e297321b63ec43f78258f6fd1d64e904d5f4113f451c59ce019ed76f91`; its source/evidence manifest: `5bd2eb51d6a6c2e7d0e4fb126fe7c0a9d4d76311481796fe2e5abb6ea50edc21`.
- TinyUSB 0.21.0 commit `dae3f9a366bfcddbf9dcf1b48d7500286a849539`. I verified all 35 files in the supplied pinned dependency manifest against their recorded hashes.

`source-manifest.json` identifies the 23 local firmware/header/doc/ARM-test inputs used by my isolated review copy. No implementation was edited by this reviewer. This report does not itself assign a Git commit to unpublished bytes.

## Source and ordering review

The writer validates its bounded length, checks the explicit gate/readiness/DTR policy, calls the actual CDC write once and returns its copied prefix count. It does not add flush counts or reinterpret a positive copy as zero after loss. TinyUSB can move accepted bytes from FIFO to an endpoint before host receipt; this behavior is visible in [the pinned upstream stream implementation](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/tusb.c). Compile-time guards require one CDC interface, non-overwritable TX and nonpersistent RX/TX. These restrictions matter because upstream otherwise permits overwrite when disconnected.

I raised two issues during the evolving draft's source review:

1. Soft pause performed `read_flush` before controller recovery even when suspended or a loss was pending. The upstream function also rearms OUT; it is not a pure memory discard. Root changed soft pause to close the gate, discard carry/framing, recover and mark a deferred RX flush without any USB call. Explicit stable/ready open performs the flush and rechecks both conditions before opening.
2. A single multibyte RX feed could dispatch another complete frame after the stable predicate changed inside an earlier dispatch. Root changed this to at most 64 one-byte feeds, checking usable state before every byte, including delimiters. A changed gate preserves the remaining suffix and stops further dispatch.

Both final paths are covered by executable regressions in the actual-core harness and my separate call-order tests. The earlier variants were inspection findings; no saved executed pre-fix reproduction is claimed. A reset inside one controller dispatch or DCD operation still needs the missing production bridge/atomicity proof.

Hard loss closes the gate before controller recovery, retains the controller object and failed-Stop state, resets application TX and clears the CDC TX FIFO. It does not use `read_flush` on the dead epoch. Reset/reopen with nonpersistent RX and a proven stale-event boundary are explicit caller obligations. A repeated current hard-loss epoch while quarantined retries recovery; opening requires the current epoch. Epoch uniqueness and correct event classification remain bridge responsibilities. DTR/suspend alone do not establish a physical reset.

The final README accurately distinguishes queued software events, FIFO acceptance, endpoint submission and fake-host completion. [Pinned CDC reset/open/read/line-state code](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/class/cdc/cdc_device.c) and [device-core queue/event code](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/device/usbd.c) were inspected. No automatic reopen or production reset-latch property is established here.

## Executable checks personally performed

### Genuine TinyUSB/core/FIFO integration with fake hardware

I verified the frozen archive/manifest, inspected the harness and copied it into an isolated directory. Against my exact firmware snapshot I personally executed all eight variants:

- Core smoke and the 14 adapter scenario groups.
- Buffered `OPT_MCU_NONE` and STM32F1 dedicated-HWFIFO configurations.
- Normal and combined ASan/UBSan builds for both configurations and targets.

Every execution passed. All four upstream translation units (`tusb.c`, FIFO, device core and CDC class) are real and unchanged. Project protocol/session/TX/controller/RX code is also real. DCD, board hooks and time are fake; this does not execute STM32 FSDEV MMIO. Upstream uses GNU11, while project/test sources use C11 with pedantic errors and warnings as errors. Leak checking is disabled and is not claimed.

The fake DCD checks endpoint open/pending/epoch state and records rejected submissions; it does not blindly return success. The suite covers real EP0 descriptor/address/configuration and DTR setup/status/ACK flow, exact 1/7/63-byte returns, FIFO pressure, all 47 CONFIG split positions plus 1/7/64 packetization, retained RX suffix, wrapping RX/TX/Stop deadlines, soft old-frame-before-new-HELLO ordering, END first-byte commitment before host receipt, hard cleanup and failed hardware Stop retry.

I separately compared all newly produced positive-prefix, submitted and completed streams: **11,520 identical bytes** per configuration/execution, SHA-256 `b903eaa836ac3f46be6aeb451c95d79066871e12dec9d264f1cf4836de325cf7`. They represent 90 synthetic FIFO cycles, not 90 acquisition windows or physical USB transfers. Evidence: eight `real-*.log` files and `real-capture-equality.json`.

Frames are formed/decoded using the production codec; this is not a new independent codec oracle. Multi-command cases are parser-plumbing stress, not approval of host request pipelining or resolution of CA-CTRL-4. The external stable flag and foreground hard-loss calls are explicitly driven by the test. Failed DCD submission demonstrates positive copy without delivery and requires external fault detection; it does not test a detection implementation.

### Separate supplemental boundary tests

My `test_call_order.c` uses explicit TinyUSB/controller stubs and compiles the actual adapter separately. **147 cases / 2,057 checks** pass strict GCC 14.2.0 and ASan/UBSan. They cover every writer return 0..64 after a mid-write gate loss, invalid/closed/DTR/readiness cases, no USB operation on soft pause, deferred flush/recheck, one-byte gating, recovery suffix retention, ordered hard cleanup and duplicate/new epoch behavior. This is call-order evidence only, not another real USB integration test.

The first harness compilation failed on my test-only misleading indentation warning. I corrected that formatting and reran without weakening assertions; `initial-harness-build.log` preserves the diagnostic. No production correction resulted from that test-authoring error.

### Negative configuration and ARM object checks

I compiled the real candidate against private copies of the actual TinyUSB config. The valid config compiled; five variants correctly failed at the explicit adapter guard: CDC count 0, count 2, TX overwrite, RX persistence and TX persistence. The original config remained byte-identical. `test_configuration_guards.py`, `guard-build/results.json` and per-case logs record the commands and outcomes.

I independently reran the root ARM script using GCC 14.2.1, Cortex-M3/Thumb, `-Os` and explicit full-size enums. Result: **604 B text, no module data/bss, context 100 B**, object SHA-256 **`04f45e1e81bfa5d691a9b5efda529da51de4268e3ead9a0b0ea9df4d245776e3`**. Step's compiler frame is 40 B; other reported frames and unresolved controller/TinyUSB calls are retained in `arm.log`. This is not a complete call-chain, ELF, linker map or timing budget.

## Remaining gates

Production use still needs reliable reset/loss observation before queue insertion, including overflow; a stale IN/OUT event barrier and safe reopen; DCD/IRQ submission/reset atomicity; automatic submission-failure handling; time-health checks before/after operations and before output; application-wide clock/ADC service budgets and stack/resource proof. The [STM32 FSDEV reset/suspend implementation](https://github.com/hathach/tinyusb/blob/dae3f9a366bfcddbf9dcf1b48d7500286a849539/src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c) does not turn a plain suspend into an unambiguous unplug detector without the necessary hardware support.

No board startup, descriptors/legal product VID/PID, physical enumeration, Windows10 interoperability, hardware timing, low-voltage or ignition result is supplied. The test descriptors use explicit zero VID/PID sentinels and must not be deployed. Shared DTR/recovery policy and CA-CTRL-4 remain open to the applicable independent review.

The bounded local review is complete. Final package integrity, reproducibility and immutable Git readback must be checked before binding this report. **BLOCKED_EVIDENCE, score null** remains the overall verdict; these passing scoped tests do not authorize a readiness label or merge.
