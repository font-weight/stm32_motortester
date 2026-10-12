# USB-001 supporting native harness

Scope: issue46, `font-weight/stm32_motortester`, stacked on base PR44
`06178f31be79c3e7dbc5b46281a67a5c1d1da7f0`. This is a **native software test**,
not STM32 execution, production USB integration, or physical enumeration evidence.

## Run

From a repository containing this directory anywhere beneath its root:

```sh
python3 tests/usb-cdc/supporting/build.py
python3 tests/usb-cdc/supporting/build.py --adapter
python3 tests/usb-cdc/supporting/build.py --hwfifo
python3 tests/usb-cdc/supporting/build.py --adapter --hwfifo
python3 tests/usb-cdc/supporting/build.py --sanitize
python3 tests/usb-cdc/supporting/build.py --adapter --sanitize
python3 tests/usb-cdc/supporting/build.py --hwfifo --sanitize
python3 tests/usb-cdc/supporting/build.py --adapter --hwfifo --sanitize
```

The runner finds an ancestor containing `firmware/transport/mt_usb_cdc.c`; dependency
root defaults to `firmware/third_party/usb`. Relocation overrides:

```sh
USB_FIRMWARE_ROOT=/absolute/repository \
USB_DEPENDENCIES=/absolute/usb-dependencies \
python3 /absolute/supporting/build.py --adapter --hwfifo --sanitize
```

`CC` defaults to `gcc`. The firmware source list is explicit: acquisition,
capture_link/control/control_rx, command/session/stream/wire, mt_tx and mt_usb_cdc.
No platform/ADC peripheral/timebase C sources are accidentally pulled in.

Upstream's four unchanged translation units use GNU11 and `-Wall -Wextra`.
Project/test sources use C11, `-pedantic-errors -Werror -Wall -Wextra`;
upstream headers are marked external for those strict project compilations.
The real upstream sources are compiled separately with ordinary include paths,
so their warnings are retained. Sanitized builds combine ASan+UBSan for every
linked object and executable, with `detect_leaks=0`; **no LSan claim**.

## What is real and what is fake

Real, unmodified TinyUSB0.21 commit
`dae3f9a366bfcddbf9dcf1b48d7500286a849539`:

- `src/tusb.c`
- `src/common/tusb_fifo.c`
- `src/device/usbd.c`
- `src/class/cdc/cdc_device.c`

All protocol/session/TX/controller/RX calls use the real firmware sources.
There are no replacement CDC write/read, FIFO, core, or CDC-class implementations.
Only the device-controller hardware, board hooks, and clock are fake.

The fake DCD tracks endpoint open/pending/stall/epoch state. It rejects and counts
invalid submissions instead of unconditionally returning true. Submissions and
host completions have separate byte logs. Host IN completion can be delayed;
OUT data enters the real endpoint buffer or real FIFO, followed by real
`dcd_event_xfer_complete` and `tud_task_ext`. Tests fail if any unexpected invalid
submission occurred. GPIO/MMIO/interrupt preemption and USB bus signaling are not
represented.

EP0 executes GET_DEVICE_DESCRIPTOR, SET_ADDRESS, short/full
GET_CONFIGURATION_DESCRIPTOR, SET_CONFIGURATION and CDC SET_CONTROL_LINE_STATE
through setup/data/status completion. DTR callbacks are never called manually.
Descriptors are **test-only**, interfaces0/1, notification0x81, OUT0x02/IN0x82,
MPS64. VID=0/PID=0 are deliberate invalid product-identity sentinels; do not deploy.

Two configurations run independently:

1. `config-f1/tusb_config.h` is byte-identical to recovered ARM evidence config,
   with `STM32F103xB` compiler define. This selects upstream's dedicated HWFIFO
   path. Fake `dcd_edpt_xfer_fifo` uses **real** `tu_fifo_read_n` at IN submission
   into a64-byte fake hardware packet and `tu_fifo_write_n` upon OUT reception.
   It does not compile or simulate STM32 FSDEV MMIO code.
2. `config-buffered/tusb_config.h` changes only MCU selection to `OPT_MCU_NONE`
   and explicitly supplies `TUP_DCD_ENDPOINT_MAX=8`. This exercises upstream's
   separate CDC endpoint-buffer path. FIFO/EP sizes, non-overwrite/nonpersistent
   settings,16-event queue and one event per task run are identical.

Each fixture asserts the actual chosen OUT path; logs print HWFIFO=0 or1.

## Executed acceptance groups

The frozen evidence contains eight successful executions: core smoke and adapter,
for both configurations, normal and combined ASan/UBSan.

- 90 real FIFO cycles/wraps, 11,520 positive-prefix bytes, exact short returns
  1/7/63, full-FIFO zero return, and byte-for-byte host completion equality.
  A64-byte IN transfer plus64-byte FIFO can both be outstanding.
- Closed-by-default and argument checks; optional/required DTR via real EP0;
  repeated and RTS-only changes; stable gate; queued suspend before task dispatch.
- CONFIG split at every0..46 boundary and1/7/64-byte packets (50 cases).
- Two complete frames in one read, and recovery at the exact first delimiter
  retaining the next HELLO suffix. These are parser-plumbing stress cases, **not
  acceptance of host request pipelining or a change to outstanding-request policy**.
- Silent RX1000/3000ms deadlines, including uint32 wrap; TX1000ms no-progress,
  TX3000ms absolute-frame despite fresh progress, and STOP1000ms despite progress.
- Soft recovery keeps accepted EP/FIFO bytes and partial TX. Same nonce remains
  ignored; old complete frame precedes new HELLO_ACK sequence1.
- Soft pause during suspend/pending loss makes no DCD submission. RX flush/rearm
  waits until explicit stable reopen. Gate loss during the first command prevents
  the next command in the same64-byte read from dispatching.
- Zero acceptance does not consume sequence. END first positive and FINISHED can
  occur before any host receipt. STOP before/after END commitment emits one
  terminal and preserves old reply ordering before STOP_ACK.
- Explicit foreground hard loss with pending IN, full TX FIFO, partial mt_tx,
  partial framing and unconsumed64-byte carry clears application TX/RX carry,
  preserves diagnostics/last nonce/failed hardware-running state, and calls Stop.
  Real BUS_RESET produces no unmount callback; real reconfiguration isolates
  old buffers. New HELLO retries hardware Stop and ACK starts at sequence1.
- Fake reset inside DCD submission and rejected submission after FIFO copy
  preserve the actual positive64 return; no host delivery is inferred.
- Explicit deconfiguration/unplug foreground quarantine, repeated hard epochs,
  and fake reset during deferred RX rearm keep reopening gated on the latest
  explicit external assertion.

## Hard limits and pending proof

`stable` is manually controlled external test state. Calling foreground
`hard_loss` directly is **not** a production reset-latch implementation.
No claim is made that this harness implements or verifies:

- reset detection before event-queue insertion, including queue overflow;
- stale queued IN/OUT event barriers or safe automatic reopen;
- production DCD/IRQ reset/submission atomicity, ARM MMIO races or real IRQ timing;
- automatic DCD submission-failure detection (the test explicitly closes its
  external gate after demonstrating a positive copy with no delivery);
- production timebase-health gating, board descriptors/legal VID/PID, startup,
  linker/ELF/flash map, clocks/VBUS/USB pins, physical enumeration or Windows10;
- ADC service deadlines or physical sampling accuracy.

Those remain **NOT IMPLEMENTED / NOT TESTED** for production integration.
No tests modify `host/**`; no host protocol policy is approved by this result.
ARM-object evidence belongs to the separate adapter/dependency object checks.

## Evidence and integrity

`evidence/matrix.json` records outcomes and artifact hashes. Each configuration
subdirectory contains raw compile/run logs, full command+input hash receipts,
actual positive/submitted/completed binary streams, and the submission CSV.
`evidence/upstream-sha256-check.log` checks recovered pinned upstream files.
`SHA256SUMS.harness` covers the supporting source/config/evidence bundle.
Build executables/objects are regenerated, not included as source deliverables.

Frozen implementation tested:

- mt_usb_cdc.c SHA256 `4127541694a00521f0eddce314bbce8383da892b2a5adbb0e5bace6576d0193f`
- mt_usb_cdc.h SHA256 `9ab7a12dcfb1d0d3282d151f0c07884d8b30fcee3cd4e4ef893b6bc13a78f7b4`

Compiler: Debian GCC14.2.0. These receipts are source-hash evidence; a reviewer
must still bind them to the exact publication Git SHA. No merge/readiness or
full-system acceptance follows from these tests.
