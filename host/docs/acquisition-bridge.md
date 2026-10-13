# Qt acquisition bridge (draft)

This bounded host-only integration joins [runtime](runtime.md),
[session controller](session-controller.md), [serial transport](transport.md)
and the explicit [byte-peer emulator](device-emulator.md). It does not change
those modules' protocol source pins or accept the shared wire contract.
Independent Critic-B review at **the exact candidate version**, with a score
of at least 9.5/10 and the required evidence, is a separate gate under
[quality-gate.md](../../docs/quality-gate.md). Author tests do not grant acceptance.

## Ownership and small public API

`AcquisitionBridge` is a GUI-thread `QObject`. Its worker is a separate `QObject`
in a dedicated `QThread`; a worker-owned `QTimer` invokes one bounded runtime
pump every 10 ms. The thread starts only on the first explicit connection.
All serial transport construction and backend create/open/read/write/close calls
happen in this worker. No serial object or mutable runtime crosses into the GUI.
GUI creation, painting, file dialogs and the existing file I/O worker remain
outside this module.

Read-only GUI properties:

- `state`: frozen `BridgeState`, containing `source`, `connected`, `snapshot`,
  `busy`, `start_pending`, `closing`, `reset_required`, `close_failed`,
  `has_capture` and `has_incomplete`.
- `state.snapshot`: frozen session `Snapshot` or `None`; phase is
  `state.snapshot.phase`, a `session.Phase` enum. `connected` means the transport
  is open, not that handshake is complete; use READY plus `busy == False`.
- `running`: whether the worker still owns an unjoined native thread. It stays
  true through native finalization even after Qt reports `isRunning() == False`.
  A disconnected worker can remain running until shutdown to transfer results
  or reconnect.
- `active`: whether a connection exists or a worker command is pending/executing.
- `pending_capture`, `pending_incomplete`: exact objects delivered to the GUI.

GUI-only methods return `True` when a bounded request was accepted, not when the
protocol action succeeded. A local invalid/busy request returns `False`.

- `connect_source("emulator")` or
  `connect_source("device", port="COM3", reset_confirmed=False)`.
- `start(Config(...), simulate_edge=True)`.
- `stop()` and `disconnect_source()`.
- `acknowledge(capture)` and `acknowledge_incomplete(result)`.
- `shutdown()`; observe `finished` before destroying the bridge/thread owner.

Signals are `state_changed(BridgeState)`, `capture_ready(Capture)`,
`incomplete_ready(WindowResult)`, `error(str)` and `finished()`.
Status/capture signals are delivered on the GUI thread. Payloads are immutable.
A caller must retain or display the exact capture **before** acknowledging it;
an equal copy is rejected. Retention is in-memory ownership, not a claim that a
file was saved. Saving/opening/exporting files stays in the existing GUI file
worker. Incomplete results contain original fragments and missing ranges;
they are never padded to look like a complete capture.

Constructor injection of `serial_factory`, `clock_ms`, `interval_ms` and
`edge_delay_ms` supports bounded author tests. Factory results must implement
`SerialTransport`'s interface and ownership contract; production uses the real
`SerialTransport` constructor. The GUI must not access worker-private fields.

## Start, Stop and sources

Connection sends only HELLO/GET_CAPS. It does not configure or ARM. Start sends
CONFIG and waits for a validated CONFIGURED event before it stages ARM. A
queued Stop cancels a queued or CONFIG-waiting Start; no later CONFIG ACK can
restart it. Already executing transport calls cannot be interrupted. An ARM
whose control transition already executed is an in-flight command and may be
followed by STOP; the bridge never claims to retract bytes already accepted.

The emulator is always constructed with `all_analog_modes=True`. Its data is
labeled `origin="simulator"`; device-origin data is possible only on an explicitly
selected serial source. No port scanning, synthetic fallback or automatic
reconnection is performed. A serial factory is never touched by the emulator.

When `simulate_edge=True`, the worker waits until the emulator actually reaches
WAITING, then waits at least 150 ms before supplying a synthetic edge. This is a
GUI-selected simulation behavior, not a physical trigger. With the option off,
Normal and Single continue waiting for an edge; Auto can produce a correctly
labeled `auto_timeout` capture with no trigger index. If Auto's deadline precedes
the simulated edge, timeout wins. Single returns to READY only after release
acknowledgment; the next capture requires another explicit Start and generation.
Auto/Normal continuation uses the same generation after a complete RELEASE_ACK.

Stop has priority over queued consumer ACKs. If a Stop barrier exists, the exact
ACK waits until STOP_ACK; accepting the retained result then sends no RELEASE
and never rearms. Once a RELEASE transition had already executed before Stop
arrived, the session controller remains responsible for that in-flight command
and its barrier. Stop does not pretend that an in-flight write was undone.

## Fixed memory/event bounds

The GUI-to-worker mailbox contains one ordinary command, one exact capture ACK,
one exact incomplete-result ACK, and coalesced Stop/disconnect/shutdown flags.
Repeated submissions cannot grow a list or a Qt queued-command backlog.
An ordinary idle read does not own the control slot: an enabled Start may occupy
that one slot while the current poll finishes. A separate control-executing flag
covers commands, priority actions and consumer ACK processing. Queue admission
and the GUI busy-state merge use that flag, never the general pump-in-progress
flag. The worker publishes its resulting state atomically with releasing control
ownership, preventing an older notification from exposing a spurious idle gap.
There is no GUI retry loop. Shutdown/disconnect take priority; Stop cancels
pending Start. Locks cover only
mailbox changes and nonblocking control transitions, never serial backend I/O.

The worker-to-GUI mailbox contains one latest immutable state, one error text,
one complete capture and one incomplete result. There is at most one outstanding
Qt notification. New states coalesce; an observation timestamp by itself is not
an update. A stalled GUI therefore does not collect per-pump events or a growing
capture queue. Runtime retains at most one pending capture until the exact GUI
ACK, and then until RELEASE_ACK. The bridge cannot skip a consumer to obtain a
new capture. Holding longer than the emulator's provisional retention deadline
can cause recovery, but never silently replaces the held result.

## Recovery and asynchronous shutdown

Recovery preserves incomplete data and any unacknowledged capture. The worker
closes the transport once. If a previously GUI-acknowledged capture remains after
an uncertain RELEASE or lost acknowledgment, runtime transfers it to that same
consumer in recovery without another RELEASE. Device reconnection requires the
explicit physical-reset assertion after any previous device-open attempt; merely
opening a COM port is not reset evidence. Reconnection is blocked while results
still need consumer acknowledgment and always starts a new random nonce with
HELLO/CAPS only. Recreating an emulator is a fresh synthetic peer, not a hardware
reset claim.

`shutdown()` is nonblocking: it cancels queued commands/ACKs, ends the runtime
session while preserving results, and closes the port in the worker. It does
not initiate further ARM or RELEASE commands. It does **not** send a protocol
Stop or assert that the physical device is stopped/reset. Already executing
backend operations finish before the worker can act on shutdown. To request a
confirmed protocol Stop first, the GUI/user must use Stop and await STOP_ACK.

After a successful close, the timer stops. Qt's `QThread.finished` signal can
precede native thread finalization, so the GUI bridge polls `QThread.wait(0)`
using a context-bound timer. This is a nonblocking join check. Only after it
succeeds does `running` become false and the bridge emit its public `finished`.
The no-worker shutdown callback is likewise bound to the GUI QObject's lifetime.
The GUI must keep the owner alive until public `finished`, without calling
`terminate()` or using a blocking `wait()`. A failed close preserves
the handle and thread, exposes `close_failed`, and requires an explicit retry
using disconnect or shutdown. It does not claim successful closure or retry a
failed close in a busy loop. Open failure can leave `connected=False` together
with `close_failed=True`; the UI must still offer the explicit close retry.

Read timeout is 0 (polling), write timeout is 0.1 seconds. Those requested bounds
are not hard guarantees for an OS/driver's open/close or other blocked call.
The GUI stays responsive, but a hung driver cannot be forcibly made safe by this
bridge; shutdown completion remains unconfirmed until the worker actually exits.

## Reproducible checks and limits

With the pinned GUI dependencies available, from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host:/tmp/motortester-deps QT_QPA_PLATFORM=offscreen \
  python -m unittest discover -s host/tests -p 'test_acquisition_bridge.py' -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=host:/tmp/motortester-deps QT_QPA_PLATFORM=offscreen \
  python -O -m unittest discover -s host/tests -p 'test_acquisition_bridge.py' -v
```

`/tmp/motortester-deps` is the current Linux test environment, not a required
installation path. Author tests use actual Qt threads/event loops and byte-peer
protocol framing; a fake serial backend records all backend thread identities.
They cover source separation, handshake-only connection, Auto/Normal/Single,
immutable capture identity, bounded backpressure, Stop/ACK/start races, responsive
GUI during blocked open, incomplete results, recovery without write retry,
physical-reset gating, no auto-ARM, asynchronous shutdown and explicit close retry.
No test asserts physical USB behavior or measured sampling accuracy.

Windows 10, the Windows executable, actual COM drivers, real USB firmware,
low-voltage measurements and ignition hardware remain **untested** here.

### Lifecycle regression evidence

The earlier candidate's aggregate optimized suite crashed and a normal run hung
after eight bridge tests. Controlled GC pressure reproduced a segmentation fault
at the next lazy-shutdown event wait. Separate observation confirmed that Qt's
finished signal could arrive while `wait(0)` was still false; the old public
completion boundary was therefore too early. Those failures remain evidence,
not superseded successes. The replacement enforces the native join boundary in
production and context-binds its completion callbacks. Test cleanup additionally
awaits public completion and explicitly processes DeferredDelete while retaining
Python wrappers. Regression checks cover delayed native joining, GUI liveness
during that delay, lazy shutdown with GC pressure, and real Qt worker cycles.
Finite successful stress runs support the fix but cannot prove the absence of
all future lifecycle faults; independent exact-version review remains required.

### Idle-poll command admission regression

A later independent integrated-GUI stress run exposed a refused enabled Start.
A deterministic gated serial read reproduced READY/busy-false while the old
bridge rejected Start merely because an ordinary pump was executing. The
separate control-executing flag corrects admission without enlarging the single
command slot. Gated-read tests require one accepted Start, rejection of 100
duplicates, exactly one CONFIG/ARM and capture, and cancellation by Stop,
disconnect or shutdown before that read completes. The original negative log
remains evidence; each combined candidate still requires its own review.
