# USB-005 supporting native guard tests

Task: font-weight/stm32_motortester issue 60. Supporting tests, not independent acceptance. Production code is authored by root and is read-only to this test worker.

## Reproduce

From any directory, choose a fresh output base outside all source directories:

    OUT=/workspace/shared/usb-bridge-test-runs/your-new-independent-run
    python3 /workspace/shared/usb-bridge-tests/run.py --out "$OUT/shims"
    python3 /workspace/shared/usb-bridge-tests/run.py --out "$OUT/shims-sanitize" --sanitize
    python3 /workspace/shared/usb-bridge-tests/run.py --out "$OUT/core" --integration
    python3 /workspace/shared/usb-bridge-tests/run.py --out "$OUT/core-sanitize" --integration --sanitize
    python3 /workspace/shared/usb-bridge-tests/run.py --out "$OUT/adapter" --adapter
    python3 /workspace/shared/usb-bridge-tests/run.py --out "$OUT/adapter-sanitize" --adapter --sanitize
    python3 /workspace/shared/usb-bridge-tests/test_runner.py --out "$OUT/tooling"

Every final output directory must be nonexistent. The runner creates it exclusively, rejects even existing empty directories, and never reuses earlier evidence. Before mkdir or instrumentation it resolves symlinks and rejects output equal to or nested under the harness, bridge, epoch, dependency, prior CDC tests, or selected adapter source directory. `$OUT` above is an example; replace it for each independent reproduction.

`--adapter` includes the core integration cases and five additional actual adapter/controller regressions. Set `USB_BRIDGE_ROOT`, `USB_EPOCH_ROOT`, `USB_DEPENDENCIES`, `USB_CDC_TESTS`, and `USB_ADAPTER_ROOT` to relocate the source packages. `CC` selects the native compiler. The runner only creates artifacts in the explicitly selected output. Root must generate the patched core/DCD at `$USB_BRIDGE_ROOT/build/` first using its production patch tool.

Each output contains commands.log, receipt.json, inputs-before.json, inputs-after.json, results.jsonl (after successful execution), the executable and object files. Receipts record exact before/after source SHA-256 values, commands and exit status; generated source and binary digests are separate from immutable inputs. The inventory covers all files under selected firmware/dependency input trees, plus explicit harness, patched core/DCD, configuration, fake DCD and manifest files. It is rescanned after execution so additions and deletions are detected. Inputs are compared in a finally block even when compilation, instrumentation or testing fails; any difference prevents PASS. A rejected output receives no writes; its before/after snapshots and preservation verdict are instead emitted to stderr.

The runner independently verifies every file in SHA256SUMS.sources and fails on mismatch. Twelve tooling regressions cover source-directory aliases including symlinks, exclusive creation, source change/addition/deletion, failing work, and actual compiler failure. Mutations use synthetic disposable files; production/original inputs are not altered. This is local native evidence, not GitHub CI or a target firmware build.

Historical r1 remains under evidence/native-2026-10-10-r1, with its original runner also preserved as run-r1.py. The hardened r2 runs use fresh directories under /workspace/shared/usb-bridge-test-runs. Do not use historical default build directories for new evidence.

## Inputs and instrumentation

- Real bridge source and headers from `/workspace/shared/usb-bridge-draft/firmware/transport`.
- Real epoch module from `/workspace/shared/usb-epoch-draft/firmware/transport`.
- Patched genuine TinyUSB usbd.c from root's build directory.
- Unchanged pinned TinyUSB tusb.c, FIFO and CDC from `/workspace/shared/usb-dependencies`, commit dae3f9a366bfcddbf9dcf1b48d7500286a849539.
- F103 HWFIFO config and prior fake DCD from `/workspace/shared/usb-cdc-tests`; the original inputs are never edited.
- Adapter mode links root's actual changed mt_usb_cdc.c plus the original acquisition/controller/protocol/TX sources from `/workspace/shared/usb-adapter-draft`.

`instrument_fake.py` emits a test-only instrumented copy of that prior fake DCD. It adds critical-section assertions and per-operation counters, false-return injection for transfer and endpoint-open, mount callback observation, a strong status-complete counter, and owner pump substitution. For SET_ADDRESS, it extracts the exact complete function body from root's patched FSDEV and adds only a call counter before executing it against the fake xfer function. This checks the production address-result call placement without pretending to execute the whole hardware DCD.

The fake has real FIFO consumption and records separate copied/submitted/completed bytes. Address extraction is not a full MMIO driver test. The source of every generated artifact is hashed in the receipt.

## Current native results

GCC 14.2.0. Project sources use C11, -pedantic-errors, -Wall, -Wextra, -Werror; upstream sources use GNU11, -Wall, -Wextra. All code is compiled -O1 and -fno-lto. Sanitized runs use AddressSanitizer and UndefinedBehaviorSanitizer with halt_on_error; leak detection is disabled and no LSan result is claimed.

- 47 shim scenarios, native and ASan+UBSan.
- 40 real-core scenarios, native and ASan+UBSan.
- 45 real-core-plus-adapter scenarios, native and ASan+UBSan; the 40 core cases are repeated, not 45 new independent cases.

Assertions are the executable oracle. Printed JSON only reports success after that case's assertions pass; runner exit alone is not treated as evidence. Failure exits prevent a success summary. The receipt preserves full subprocess output.

## Coverage

### Explicit shim admission and full-call guard

Every one of xfer, FIFO, open, stall, clear-stall, address, status-complete, SOF and close-all is tested with:

1. Reset between actor admission and guarded call: zero actual calls, no false fault charged to the new generation.
2. Reset made pending after permit but before the fake's first mutation: interrupt delivery is deferred until the complete operation, including nested inner address xfer, returns through unlock.
3. Previous PRIMASK 0 and 1 restored exactly. For previous 1, the scheduled reset remains pending until the test explicitly unmasks.

Nested NVIC enable is modeled without clearing the outer PRIMASK. This is a scheduler model, not actual NVIC behavior evidence. Nested scopes preserve the old actor; reset producer does not overwrite it. Missing enter, missing leave, or both missing cause fail-closed behavior without calling either callback.

### Errors, configuration and bytes

Normal false xfer/FIFO/open and hidden address submission poison once. Subsequent denied calls are not counted as new DCD failures. A missing address hook also fails closed. Each non-success completion value, including INVALID, closes metadata at producer time; core integration suppresses both CDC callback/ZLP and EP0 address status-complete.

Enumeration DCD opens can occur before app admission. Mount cannot open the app gate. Configuration receipt is successful status submission, not host status ACK. Rejected status, reset inside mount, failed notification/bulk opens, and failed initial OUT cannot create a usable receipt. Config0 deliberately poisons before SOF/close-all/stall, and config1 in that poisoned generation cannot reopen it. This intentionally does not claim supported physical deconfiguration.

Copied64 remains returned when auto-flush is rejected or reset is deferred through its DCD operation; host0 is checked independently. HWFIFO soft-deferred TX retains its three-byte prefix until successful resume/flush. The buffered non-F1 path is not tested or supported by this package.

### Active callbacks, queue and scope lifetime

Reset inside TX callback suppresses the old ZLP; reset inside RX callback suppresses both nested read rearm and automatic rearm; vendor control callback reset suppresses old EP0 stalls. Mount reset cannot create a configuration receipt. Recursive task from a callback cannot pop the next event or replace actor.

Full queue loss of BUS_RESET, queue loss after an accepted reset, two resets, stale SETUP, dropped SETUP, soft-deferred envelope readiness, reset obsoleting held work, stale deferred functions, unscoped deferred functions, synthetic SOF/resume, one event per task, no-event returns and an in-range unmapped endpoint TU_ASSERT return are exercised. SETUP accounting recovery is checked by successful fresh configuration, not a private variable poke.

### Actual adapter/controller

The new adapter scope wrappers are compiled and executed without manually adding an outer app scope. Tests cover positive writes and argument paths, wrong physical epoch rejected on open, same-epoch sticky-fault quarantine, failed hardware Stop retained as hardware_running, Stop retry clearing it without deleting failure diagnostics, reset followed by fresh epoch open, read-rearm reset, and read-flush reset during open. The controller's hardware_running is deliberately seeded for the failure test; no hardware acquisition is implied.

## Evidence boundaries and unfinished acceptance

The integration pump calls cleaned(current) directly to isolate the bridge. It does not execute or prove a real foreground controller quarantine/Stop before every dispatch. The dedicated adapter tests do execute the real controller recovery path against a fake Stop callback. These distinct facts must not be merged into an owner-pump claim.

Not established here: real PRIMASK implementation or generated ARM code; USB HP/LP vector/priorities/PRIGROUP; IRQ latency under ADC/TIM; complete MMIO DCD behavior or physical old-transfer cancellation; actual host enumeration/reset/disconnect; PMAOVR detection; total ELF RAM/Flash/stack; full owner/ADC ARM commit gate; or Windows connectivity. INIT/DEINIT and unsupported runtime/ISO/class call restrictions require separate production source/link audit and target evidence. All nine operations have shim tests, but intentionally poisoned config0 means SOF/close-all are not exercised as a successful real-core teardown path.

Independent Critic-A review and its mandatory evidence remain separate. These tests do not issue a score, PASS for project acceptance, hardware-safe assertion, or software-ready release claim.
