# Critic-A: CTRL-001 independent review

## Verdict

**BLOCKED_EVIDENCE; score: null.** No remaining P0/P1 defect found in the bounded final software candidate. The two baseline defects and the intermediate correction regression below are closed against the frozen bytes and executable regressions. An immutable published commit binding is still pending. This is not a whole-firmware, USB, Windows, physical timing or hardware acceptance verdict.

Reviewed final controller C SHA-256: `0ef2f27441c41817867bd91336964aa65daa10732c7203fa85d97650253622ed`.
Header: `ce45cf1fa34e4c82d256c8873fee0f2901256714c91a49cca83a90c0a4868574`.
Russian README: `826731188e4c26b7d54efc61f87d20e8536bb110ada24bdd0f1dedf15a3f0a82`.
Full bytes, SHA-256 and Git blob identities are recorded in `source-manifest.json`; isolated copy: `final-candidate/`.

## Findings and resolutions

### P1 CA-CTRL-1: tombstone RELEASE restarted hardware for a different frozen window

Baseline C `0c20b280f53348e93b2cbbfce22f1d3cdc040cff865d8ad0f75ce4f04db94f2d` restarted whenever the bridge remained armed but hardware had stopped. A valid old window-1 RELEASE tombstone received while window 2 was frozen in BEGIN therefore invoked hardware start, despite no current-window release transition. This violates capture ownership and can cause unwanted conversions and loss at the hardware boundary.

I personally reproduced the original defect: start count 2 -> 3 while window 2 remained in BEGIN. `tombstone_hardware_repro.c` and `tombstone-baseline.log` preserve the evidence. The final code identifies the current WAIT_RELEASE tuple before the bridge operation and restarts only that successful transition. The unchanged reproducer now reports start count 2 -> 2, hardware stopped (`tombstone_hardware_repro-final.log`). Supporting tests also cover DATA, END, END_QUEUED and backpressure, plus prior-tombstone invalidation when the newer END commits.

### P1 CA-CTRL-2: older state events followed a newer reply or rearm ACK

Pending state observations outside TX could be overtaken by ordinary replies. Independently reproduced traces included PREPARING event seq4, CAPTURING GET_STATUS reply seq5, stale WAITING event seq6, CAPTURING event seq7; and END seq17, RELEASE_ACK seq18, stale WAIT_RELEASE seq19, WAITING seq20. A host could regress its displayed state or release ownership based on stale chronology.

The final controller flushes earlier state observations before a state-bearing GET_STATUS response or a successful state-changing CONFIG/ARM/current RELEASE operation. Insufficient admission space causes fail-closed recovery before the new action. The supporting strict and sanitizer regressions pass in my personal rerun. Unstarted obsolete observations can be cancelled by valid Stop as the shared Stop contract permits.

### P1 CA-CTRL-3: the first ordering correction stopped current hardware for a stale Stop

Intermediate C `07031e789a6a594d2b85b2dcdcfc10ce375d50b59ef31aeadf6739b2b304c74b` flushed every older observation before ERROR. A held ARM_ACK and three pending observations exhausted ordinary event admission, although the reserved ERROR slot was available. Rejecting a foreign-generation STOP consequently entered recovery and physically stopped the current capture.

I personally reproduced this regression in `stale_stop_capacity_repro.c` / `stale-stop-capacity.log`: stop count 1 -> 2, ready false. Final code admits non-state-changing ERROR, CAPS and old-tombstone ACK through the reserved reply path without this unnecessary observation flush. My unchanged reproducer now returns OK with stop count 1 -> 1 and current hardware still running (`stale_stop_capacity_repro-final.log`). Pending events are retained. Genuine status-reply capacity exhaustion remains tested as fail-closed recovery; that is a chosen bounded-queue policy test, not an additional independently discovered baseline defect.

## Personally executed evidence

- Read all controller C/header, Russian caller contract, author harness, supporting C harness and independent Python transcript oracle. No production code edited.
- Exact dependency byte comparison with prior immutable reviewed snapshots: acquisition PR16 `11f63ebb12fb9fb45cfcc1735782dd5405bf331b`; codec/command PR8 `0f285510c1b9b6bd21bb7b60ae6098f7584ae71a`; session unchanged through PR14 `c54bd955aaf3cb863ef3df043ea237ea62e74ad4`; corrected TX PR19 `826bef255362b258d5dcde2100c90dc7d591edb3`; bridge PR30 `a451e5a491439fcfed31f298a589643cf7050ec9`. See `dependency-equality.txt`.
- GCC 14.2.0 strict C11 author suite with conversion warnings as errors: passed, including 20 complete encoded synthetic windows, stale Stop preflight and hardware failure recovery. ASan/UBSan author run also passed (`author-final.log`).
- Personally reran the separate supporting suite against my final snapshot: **39/39 scenarios, 576,994 checks**, strict and ASan/UBSan exit 0. Separate Python standard-library COBS/CRC/sample oracle: **344 frames, 98,281 bytes, 20 windows, 40,960 exact sample values** (`independent-final/`). This is a rerun of supporting independent evidence, not a claim to have authored a second full oracle.
- Both personally authored targeted reproductions above pass against final bytes unchanged.
- ASan/UBSan used `detect_leaks=0`; no leak-check pass is claimed. The supporting report preserves the environment's earlier ptrace-related LeakSanitizer failure.
- Personal Cortex-M3 ARM GCC 14.2.1 compilation with `-Os -mthumb -mcpu=cortex-m3 -mfloat-abi=soft -fno-short-enums`, strict warnings, freestanding and stack-usage output passed (`arm-final/`). Controller object text **2,084 bytes**; context **8,888 bytes**, compact status 16 bytes and hook structure 20 bytes. Context includes its session, TX and capture storage, so those must not be double-counted as additional controller instances.
- Receive frame 640 bytes, bridge Stop frame 1,696, TX Stop 592, and its called shape/append frame 16 give a known **2,944-byte static Stop call chain**, before library functions, caller, IRQ, runtime and any platform callback. A 2 KiB stack is insufficient. This is not a complete worst-case stack proof or final linker fit. No startup/linker map, MCU execution or watermark was produced.

## Remaining gates

- Published exact controller commit and complete changed-tree evidence must be rebound before an immutable candidate verdict is claimed.
- Real MMIO adapter must enforce the documented canonical 72 MHz timer / PSC71 / ARR19, ADC12 MHz / 71.5-cycle PA0 profile, exclusive ownership, bounded calibration and confirmed stop/start. Lower-level 24/48 MHz backend plans must not be advertised as profile 1.
- Streaming framing/timeout/disconnect integration, actual USB FIFO acceptance and partial-write semantics, hardware Stop deadline, scheduler/WCET, DMA ownership under real bus load, full RAM/flash/stack fit, clean Windows interoperability and measured low-voltage windows remain open.
- Shared contract acceptance still needs the applicable second independent critic and integrated evidence. No merge or aggregate software-ready/hardware-ready claim is authorized by these synthetic results.
