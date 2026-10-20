# Wire 0.2 stream — DRAFT for issue #191

Author: Dot A. Base: main `96002ce62bb5c065ac665ca81059c4a62e90d1dd`.
This document specifies a draft endpoint contract with separately published draft
implementations. Publication does not establish shared acceptance or hardware
qualification. Exact-version review by both teams and independent critics remains
required; see ../docs/progress.md for version-bound implementation evidence.
No field below may be enabled in the existing wire 0.1 firmware by implication.
The known window candidate in PR190 and old segmented archives remain unchanged.

## 1. Scope and user-visible boundaries

One channel, profile 1, nominal 50000 samples/s, unsigned 12-bit ADC codes in
little-endian u16 words. This is a throughput target, not a measured guarantee.
A stream starts only after an explicit Preview or Record action. Opening a port
or reading capabilities does not arm the ADC. During preview, Record selects the
next whole validated DATA block after the host writer is ready. It records the
actual first absolute sample index R; it does not claim R is the button timestamp
or the first sample acquired by the device. If END/fault wins the race before the
next selected block arrives, report no recorded samples and no established R; do
not fabricate a successful indexed interval. If Record is invoked without preview,
prepare storage before START and select R=0. The receiver/storage boundary must
make the selection atomic; display refresh cannot choose R or discard raw data.

Stop ends device acquisition and the current recording. A return to preview
requires a new generation. In-stream CONFIG and changes of sample rate are not
supported. This revision has no automatic repeated windows and no RELEASE.
A disconnect or a new HELLO terminates the old stream, never joins two timelines.

Host waveform-event bookmarks use original samples, configurable context,
hysteresis and holdoff. Events may cross DATA boundaries. Missing prehistory,
truncated tails, clipping and overlap remain visible. This is waveform-event
navigation, not burn diagnosis, cylinder attribution or proof of a full physical
spark. No external trigger or first-cylinder input is introduced.

## 2. Framing and compatibility

Reuse ordinary COBS plus delimiter 00 and CRC-32/ISO-HDLC from wire-v0.1.md.
CRC covers header and payload before COBS, stored little-endian. Header offsets:

| Offset | Bytes | Meaning |
| ---: | ---: | --- |
| 0 | 1 | major=0 |
| 1 | 1 | minor=2 |
| 2 | 1 | type |
| 3 | 1 | reserved=0 |
| 4 | 2 | payload length, at most 512 |
| 6 | 2 | reserved=0 |
| 8 | 8 | connection nonce |
| 16 | 4 | transport sequence |
| 20 | 4 | request id |
| 24 | N | payload |
| 24+N | 4 | CRC |

Maximum decoded frame 540 bytes; ordinary COBS upper bound 543, plus delimiter
544. Every fixed payload length must match exactly; reserved values must be zero.
No C struct layout is a wire ABI. A CRC-valid incompatible revision is an explicit
incompatibility, never an instruction to try ARM. A timeout is an unknown failure,
not proof of an old device. No automatic fallback or automatic arming under 0.1.
An explicit user-selected legacy mode may establish a separate 0.1 connection.

Validation order: bounded framing/CRC/version/header; direction/type admissibility; special HELLO;
nonce; exact sequence; request correlation; payload and state. Bad framing/CRC
is discarded without executing a command. A subsequent sequence gap is fatal.
While awaiting START_ACK or in STREAMING/DRAINING, host treats any nonempty
malformed/CRC-invalid incoming frame as a stream-integrity failure, without trusting
its type/session fields. There is no skip-and-join recovery. Only handshake recovery
has the bounded old-tail discard exception below. Device command corruption cannot change state;
host command timeout invokes stop/recovery and marks absent terminal evidence.

A hardware quiescence fault is a separate sticky interlock, not a protocol state
that a nonce can clear. Every HELLO-to-STOPPED transition and every START requires
a successful driver quiescence/ownership check. If that check fails, enter
FAULT_HARDWARE: do not send HELLO_ACK, do not publish a new STOPPED session and do
not permit START. A fresh HELLO may retry the actual bounded stop barrier, but only
its verified success can clear the interlock. Otherwise physical power reset and
validated startup are required. The host sees a failed/unknown handshake, not a
repaired device. Reset or reconnect alone is not proof unless startup establishes
the same hardware-off condition. All state-table transitions are conditional on
this interlock.

HELLO has header nonce=0, sequence=1, request=1; payload fresh nonzero u64 nonce.
Before invoking its barrier, retain the last attempted valid HELLO nonce even
when the barrier subsequently fails. A nonce equal to the current or last attempted
nonce is ignored without another barrier, reset or reply. A genuinely fresh nonce
may retry the real bounded barrier; it cannot clear the hardware interlock alone.
A fresh nonce in any state initiates the bounded stop barrier, inhibits new START
and abandons old not-started TX frames. It does not prove sampling stopped if the
barrier fails; the hardware interlock and unknown quiescence remain explicit.
Finish an already-started frame before HELLO_ACK, except physical USB reset may
truncate it. Host ignores
old-nonce frames, uses a fresh nonce on every recovery and retains old recording
as incomplete when no matching terminal was received. Nonces are correlation,
not authentication. USB disconnect/reset immediately disables acquisition.
On the device, a valid old-nonce ordinary frame is ignored before sequence/request
or payload/state validation. Here valid means bounded envelope, CRC, exact revision,
header and direction/type admissibility have passed. Wrong-direction or unsupported
active-phase types are not hidden by a stale nonce. This discard executes no command
and consumes no current-session sequence. It is not an authenticity/security claim.
Ignored old-nonce input does not replace the previous accepted-frame duplicate history.

After HELLO_ACK both direction sequences start at 2 and increase by one; no wrap.
Host request IDs increase strictly from 2 (gaps allowed); events use request=0,
replies echo the request. Immediate byte-identical duplicate of the most recently
accepted frame may be ignored without applying it twice; any other old, duplicate,
conflicting or reordered frame is fatal. No DATA retransmission with a new sequence.
Byte-identical means the exact encoded frame bytes, excluding its delimiter,
not merely equal decoded fields. Retain the bounded previous accepted encoded
frame for this check; alternative valid COBS encodings are not identical duplicates.
Begin controlled counter-limit termination before assigning ordinary DATA transport
sequence 0xffffff00 or accepting an ordinary request_id at/above 0xffffff00. The
remaining 256 sequence values are reserved only for bounded tail and control work.
A terminal drain contains at most32 already-certified DATA blocks (including a
started frame and final partial) plus at most4 control/terminal frames. Implementations
with larger queues must change this contract, not assume the reserve is sufficient.
STOP may use reserved request/sequence values; other ordinary commands may not.
Never wrap u32 counters; refuse START without this reserve. Fresh HELLO has its
special sequence1 and does not consume the exhausted old-session counter. Before each sample-block addition, refuse an
exclusive end above UINT64_MAX; the limit itself may be an end but never a reused
start. A fresh HELLO is required after counter-limit termination.

TX sequence commits at first accepted encoded byte, not enqueue. One TX owner
finishes a started frame before another. Dropping a not-started frame cannot create
transport sequence gaps. Unknown accepted-byte count is a transport fault requiring
session recovery, not a retry. A reply/terminal capacity reserve must be independent
of data-queue occupancy.

## 3. Messages (all integers unsigned little-endian)

Types not listed are unsupported. Known wrong-direction messages cause recovery.
Unknown host type in an otherwise valid idle exchange gets ERROR; unknown device
type is fatal to the host. During STOP drain, any new ordinary request causes
recovery without adding an ERROR; HELLO is the sole recovery exception.
Unknown host types in STARTING or STREAMING likewise require recovery without
an extra ERROR. Known wrong-direction types fail before nonce handling in all phases.

| Type | Direction | Name | Payload in order | Bytes |
| ---: | --- | --- | --- | ---: |
| 1 | H>D | HELLO | nonce:u64 | 8 |
| 2 | H>D | GET_CAPS | empty | 0 |
| 8 | H>D | STREAM_START | profile:u16=1, reserved:u16=0 | 4 |
| 5 | H>D | STOP | generation:u32 | 4 |
| 129 | D>H | HELLO_ACK | major:u8=0, minor:u8=2, channels:u8=1, adc_bits:u8=12, max_payload:u16=512, max_block:u16=128, firmware_build:u32, capabilities:u32=1 | 16 |
| 130 | D>H | STREAM_CAPS | config_id:u32=1, profile:u16=1, channel:u8=0, adc_bits:u8=12, fs_num:u32=50000, fs_den:u32=1, max_block:u16=128, reserved:u16=0, flags:u32=1 | 24 |
| 136 | D>H | STREAM_START_ACK | generation:u32, config_id:u32=1, first_index:u64=0 | 16 |
| 152 | D>H | STREAM_DATA | generation:u32, config_id:u32, first_index:u64, count:u16, reserved:u16=0, count*u16 codes | 20+2*count |
| 153 | D>H | STREAM_END | fields below | 40 |
| 133 | D>H | STOP_ACK | generation:u32, prefix_end:u64, terminal_sequence:u32, flags:u32 | 20 |
| 255 | D>H | ERROR | rejected_type:u8, code:u8, reserved:u16=0, generation:u32 | 8 |

Capabilities bit0 means this stream profile only; all other bits zero.
CAPS flags bit0 means nominal timing unverified; all other bits zero. Rate fields
must match this profile, not an arbitrary inferred/measured rate. Both response
messages must agree; inconsistent capabilities forbid START. Firmware_build is an
identifier only, not a cryptographic image identity. Stream START is permitted only
after GET_CAPS in that connection. Profile is immutable; config_id=1 uniquely names
this fixed profile within the nonce. No legacy CONFIG payload is accepted under0.2.

START generation equals its request id. Queue capacity must exist for the response
before hardware start. Send START_ACK only after successful driver start; it means
sampling was enabled, not that samples reached the host. On a failed start, send
ERROR(code=4 START_FAILED), do not send START_ACK or DATA, and require a new HELLO.
Successful START_ACK precedes every DATA/END of that generation. Stop arriving
while START_ACK is pending targets that START request id. If START failed, reject
STOP locally as stale and require HELLO: no STOP_ACK or additional ERROR beyond
the already-reserved START_FAILED response. Never acknowledge stopping a nonexistent stream.

DATA count is 1..128, codes 0..4095. Full blocks contain128 samples; only the final
block may be shorter. Start at index0 and require next.first_index=previous end;
addition is checked without u64 wrap. No missing index, overlap or reordered block
is repaired. Source index increments for actual acquired samples, not packet time.
A missing block causes termination/recovery with the verified prefix retained.
Host arrival timestamps must not be used to fill sample-time gaps.

## 4. Terminal evidence and Stop

STREAM_END has request=0 and these exact offsets:

| Offset | Bytes | Field |
| ---: | ---: | --- |
| 0 | 4 | generation |
| 4 | 4 | config_id=1 |
| 8 | 8 | prefix_end: exclusive index after last DATA serialized before END |
| 16 | 8 | produced_end: exclusive acquired end, or zero when unknown flag set |
| 24 | 4 | last_data_sequence, zero if no DATA |
| 28 | 4 | prefix_crc32 over every raw little-endian sample from index0 to prefix_end |
| 32 | 2 | reason |
| 34 | 2 | flags |
| 36 | 4 | stop_request_id, zero for unsolicited failure |

Reasons: 0 user Stop; 1 queue overrun; 2 DMA service/ownership fault;
3 invalid clock; 4 counter limit; 5 stop/quiescence failure; 6 transport drain
failure. Flags: bit0 produced_end unknown; bit1 incomplete tail; bit2 hardware
quiescence not established. Other bits zero. Unknown produced_end implies bit1
and produced_end=0. With known produced_end, require produced_end>=prefix_end;
unequal values require bit1. Reason0 and flags0 require equality. Error reasons
remain errors even if no known samples were lost. CRC of an empty prefix is0.
No counter or terminal value proves physical calibration, authenticity or host disk
flush. A matching received END plus checked prefix is stronger evidence than ACK.

On first acquisition fault: latch its cause, disable acquisition, retain the valid
queued prefix and enter DRAINING. No later sampling or rearm in that generation.
On user STOP: disable the trigger timer and stop DMA/ADC by the driver ownership
barrier, then establish the stable DMA endpoint before collecting a final partial
block. If the endpoint is ambiguous, set unknown/incomplete; never invent a count.
If stop quiescence fails, set bit2 and forbid clean STOP_ACK. This driver snapshot
and partial-block handling require new implementation/tests; not proved by this doc.

Drain only already-certified bounded data. Order: pending START_ACK, DATA prefix,
STREAM_END, matching STOP_ACK. No DATA after END. prefix_end and CRC cover exactly
the DATA frames completed into the transport before END, not merely enqueued data.
A host must compare END against its received prefix and validate last_data_sequence.
If Record began at R>0, the receiver still checks rolling CRC from index0; archive
stores the selected interval [R,prefix_end) and separate recording CRC. Do not claim
a device whole-prefix CRC directly verifies only that selected subrange.

At most one non-duplicate STOP request is accepted per generation, including a
late STOP after unsolicited END. After that request is accepted, another STOP
with a new request id requires recovery without another reply; the immediate
byte-identical duplicate rule remains unchanged and does not enqueue an ACK.
After the single STOP_ACK completes, enter NEEDS_HELLO: only a fresh HELLO may
create a new usable session. Thus repeated late STOP requests cannot consume
unbounded reserved sequences or extend any response deadline.

STOP_ACK repeats prefix_end, END's transport sequence and END flags. It is sent only
when the stop barrier established quiescence; bit2 therefore forbids STOP_ACK and
requires recovery. It does not mean DATA is received, archive is durable or capture
had no loss. A host finalizes cleanly only after matching END reason0/flags0, all
selected samples, matching STOP_ACK and successful durable host finalization.
A failure END may still receive STOP_ACK confirming the stop operation; retain the
original failure cause. If a fault already entered DRAINING and STOP arrives before
END starts, include that request id. If STOP arrives after END has started (including after its complete transmission),
leave the frozen END unchanged and enqueue one ACK referencing that END sequence.
Its END.stop_request_id remains0 for an unsolicited failure; the matching ACK
request_id identifies the later Stop. Do not emit a second END or rearm. The finite
data-drain deadline still governs an END not yet completed. For a Stop received
after a completed unsolicited END, a separate500ms control-response deadline starts
at receipt; it permits only ACK/error, no more DATA or new terminal. Failure to
deliver that response by its deadline requires FAULT_NO_TERMINAL/recovery, retaining
the already-received END as evidence, not deleting it. A STOP while an END is in
flight uses the remaining original drain budget, never extends that budget.

The termination budget starts on the first transition into shutdown caused by
Stop, acquisition fault or counter limit, BEFORE invoking any driver stop/barrier.
Latch that monotonic time once; the absolute deadline is that time plus500ms.
A failed barrier, a stop retry, duplicate request, later Stop or TX progress cannot
restart it. This origin remains defined even when hardware never becomes quiescent.
It includes all queued bytes
and any already-started frame. Check the absolute deadline independently of TX
progress; trickle progress does not extend it. Before expiry, finish a started
frame and drain certified queued data in order. At expiry, stop the old-session TX
path, abandon pending DATA/terminal/ACK and enter FAULT_NO_TERMINAL. The receiver
may have an incomplete frame: no terminal or ACK is guaranteed. Host timeout
classifies the tail as unknown and requires a fresh connection/HELLO (with bounded
framing resynchronization as specified below), never silently joins the next stream. A deadline failure
cannot be reported as a clean terminal merely because some old bytes later arrive.
New acquisition starts remain inhibited regardless of transport recovery;
actual hardware-off is asserted only after the real barrier proves it. Reason6 is reserved
for a detected drain failure that can be reported before the absolute deadline;
at/after the deadline the first local fault is retained without promising delivery.
After any timeout/recovery, host resets its local frame accumulator and sends an
extra00 delimiter before the fresh HELLO. Device treats this as ordinary delimiter
resynchronization; only a fully valid fresh HELLO establishes the new connection.
In FAULT_NO_TERMINAL, an abandoned device TX frame is not resumed. On receiving
that fresh HELLO, device writes00 to delimit any old truncated device frame, then
sends the new HELLO_ACK. Already accepted FIFO bytes may precede this delimiter;
they must not be reordered after it. If that transport ordering cannot be assured,
require physical USB reset/reconnect instead of acknowledging the new session.
During handshake only, host may discard bounded partial/invalid old-tail frames
until matching fresh-nonce HELLO_ACK(sequence1/request1); it cannot publish samples
or treat another response as successful handshake. Failure to receive ACK within
the absolute HELLO request deadline defined below leaves disconnected/incompatible-unknown,
with no automatic START. Reopening
a CDC handle alone does not prove old bytes were removed. In healthy old sessions,
HELLO still finishes the started old frame first, then the new ACK as described above.

### Device deadline service and HELLO budget

Every valid fresh HELLO has a separate absolute500ms service budget. Start it at
completion of full HELLO validation, before any quiescence call or old-TX handling.
It covers the barrier, old started frame, any recovery delimiter and the complete
HELLO_ACK. A superseding HELLO never extends an already-active frame's budget,
including an earlier HELLO_ACK budget. While that old frame remains live, the
earlier applicable deadline wins. Expiry abandons unaccepted output, suppresses
the pending new ACK and leaves acquisition inhibited; it does not create STOPPED
or clear a hardware interlock. A failed barrier likewise sends no HELLO_ACK.

For all device500ms budgets, expire at `now >= deadline` before processing a
completion at that instant. Service timers independently of RX/TX activity and
again after any bounded callback returns. Late wake or progress never moves a
deadline. The late-Stop response budget after completed unsolicited END uses the
same comparison and begins when that complete Stop request has been validated.

Driver/transport calls must be bounded; a nonreturning callback cannot be rescued
by a later timer check. In particular, checking only AFTER a write is insufficient:
an already-accepted late END/ACK cannot be retracted. Before invoking TX, its adapter
must guarantee that accepted bytes enter the ordered FIFO AND that the callback
returns with completion observable AND that the immediately following completion
validation, budget discharge and any session/state publication all finish strictly
before the earliest active deadline. Use a verified worst-case transaction bound B
(write plus the entire completion decision/state commit) and checked admission guard
`now + B < effective_deadline`; equality refuses the call. A deadline-aware write
alone is insufficient if its completion or post-return state commit can be delayed
too late. Include post-return preemption through the final state store in B, or
provide an equally strong atomic commit seam. If the adapter/endpoint composition
cannot guarantee the full bound, do
not issue the write; expire/recover rather than claim a timely terminal. No new
bytes may be accepted at/after the deadline. Any observed late/unknown acceptance
is a transport-contract failure; retain diagnostics and never promote the local
result to clean completion. This guarantee requires real adapter evidence and is
not established by synthetic callback tests. Host2000ms/1000ms deadlines remain
independent and do not prove the device met its500ms obligations.

The effective write deadline is the minimum of every still-active termination,
late-Stop response and HELLO budget governing that output. Keep the original
budget identities through supersession; do not relabel old live bytes as new work.
A termination budget is discharged only when its complete END and any already
pending matching STOP_ACK have been accepted in time; without a pending Stop,
timely complete unsolicited END discharges it. A later Stop starts its separate
response budget. A HELLO budget is discharged only by timely complete HELLO_ACK;
a late-Stop response budget only by timely complete matching ACK. Failure/abandonment
closes the budget as failed, never as successful completion. A frame frozen before
expiry but not completely accepted does not discharge its budget.
Abandoning not-started frames may mark their parent budget failed, but does not
retire that budget's deadline while an already-started frame still inherits it.
Keep that inherited bound until the old frame completes with timely observed
acceptance, or until transport isolation certifies it can append no further bytes.
Only then may the failed old budget cease governing later new-session output.
An expiry also fails/suppresses any already-pending new HELLO_ACK; later isolation
does not revive that HELLO. A further fresh HELLO is required after recovery.
Publish the new STOPPED session only after verified quiescence and complete
HELLO_ACK acceptance observed strictly within every applicable active budget.

Acceptance means entry into the adapter's ordered irrevocable FIFO, not physical
USB delivery or HOST validation. Bytes accepted before expiry may be delivered
later. The qualified write bound must include preemption, scheduling, time reads
and call/return overhead; it is not just an average measured write duration.
Use valid non-wrapping monotonic time and checked deadline arithmetic. If either
is unavailable or timing becomes uncertain, fail closed and retain that uncertainty.
Software inhibition/timeout alone never proves hardware off; failed quiescence
retains its independent sticky interlock and first-cause evidence until a real
barrier establishes otherwise.

Host liveness timers use its monotonic clock: the first valid DATA or END must
arrive within1000ms of START_ACK; thereafter each complete valid DATA restarts a
1000ms next-DATA-or-END deadline. A nonempty partial frame has an independent
1000ms absolute deadline from its first byte, never extended by trickle bytes.
Delimiters, invalid frames and duplicate frames do not count as data progress.
A matching END ends the DATA liveness timer; awaiting STOP_ACK still uses its
request timer. During a pending Stop, whichever applicable liveness/request timer
expires first causes incomplete recording and recovery. Any expired timer retains
the verified prefix and first cause; no automatic same-session retry or interpolation.
### Host request admission, transmission and response deadline

HELLO, GET_CAPS, STREAM_START and STOP each use one absolute 2000 ms budget.
The start event is atomic admission into the bounded pending-request slot: bind
the request type, nonce, request id, intended sequence and monotonic timestamp
`t_admit` before any transport write. The deadline is `t_admit + 2000 ms`.
For recovery HELLO this includes sending its preceding resynchronization delimiter.
If the required slot is unavailable, reject the action locally without allocating
a pending request or sending bytes; never place it in an unbounded pre-admission
queue. The pending identity is not replaced by later actions or failed writes.

At every send/receive dispatch, first expire any applicable deadline when
`now >= deadline`, then process completion/progress only if still live. A
timer wake must also be scheduled independently of send/receive activity: silence
after a complete write cannot postpone expiry. If the scheduler wakes late, expire
before processing any queued completion; the deadline itself never moves. A matching
response must have been fully received, validated and correlated at a monotonic
time strictly before the deadline. Bytes merely buffered by the OS before expiry
are not proof of timely validation. Equality is timeout, including a response or
last write completion observed at that exact instant. Use a non-wrapping monotonic
clock with sufficient precision; checked arithmetic must not turn overflow into
an extended budget.

Queue wait, zero-byte acceptance, partial acceptance, trickle progress, completing
the request write, unrelated responses and duplicate frames do not restart or
extend this budget. Transport writes must be nonblocking or bounded by the earliest
remaining applicable deadline; a blocking write cannot postpone timeout handling.
Only one TX owner writes frames. A STOP admitted behind a partly sent ordinary
request has its own admission deadline but cannot interleave into that frame.
The earlier applicable timer still wins; later STOP admission does not cancel an
outstanding ordinary request deadline. Sequence commitment remains at the first
accepted encoded byte as specified above, independently of admission.

On expiry, stop issuing the unaccepted suffix and abandon not-started queued
requests of the old session. Retain the timed-out pending identity, accepted-byte
count if known, verified sample prefix and first failure cause. Even if zero bytes
were accepted, do not retry the same request/session automatically. A partial or
fully accepted request has an uncertain remote outcome. Enter recovery using a
fresh HELLO and the bounded resynchronization rules; never splice an emergency
STOP into an unfinished encoded frame. Accepted bytes may still arrive and are
old-session evidence, not a reason to revive an expired request. A failed HELLO
leaves the host disconnected/unknown with no automatic START. This host timeout
does not itself prove that acquisition stopped; actual quiescence remains a
device/connection recovery requirement.

Before writing the recovery delimiter, establish that no outstanding old write
can append bytes after it. Cancellation is not assumed to retract bytes already
accepted by the OS/device. The transport adapter must prove completion/cancellation
isolation and FIFO ordering within an absolute 2000 ms cleanup budget starting at
entry to recovery (when the failure is detected, before any cancellation call).
Cleanup progress does not extend it; `now >= cleanup_deadline` is failure and is
checked independently of I/O activity, before accepting a completion at that instant.
If this isolation cannot be established within that budget, require physical USB
reset/reconnect and validated startup rather than sending a new HELLO through an
uncertain writer. Do not block indefinitely on cancellation. The recovery HELLO
budget starts only after this isolation is established and its slot is admitted;
failure to establish isolation leaves disconnected/unknown, never a usable session.

Only one ordinary request (including HELLO) plus one Stop may be outstanding;
STOP is not admissible during HELLO negotiation or before a START generation is
known. No new ordinary requests while Stop pending. No automatic retry
START/STOP after uncertain outcome. Cleanly ended streams require a fresh HELLO
before another START in this initial revision; session separation is preferred over
an implicit same-session reset. Thus preview restarts with a fresh nonce/generation.

## 5. State and failure table

| State | Allowed actions | Outcome |
| --- | --- | --- |
| NO_SESSION | fresh HELLO | STOPPED; HELLO_ACK |
| STOPPED | GET_CAPS then START; fresh HELLO | STARTING or new connection |
| STARTING | matching Stop | finish successful START response first; Stop ordering |
| STARTING | fresh HELLO | barrier/recovery; only an already-started old frame may finish; abandon unstarted responses |
| STREAMING | matching Stop; fresh HELLO | DRAINING or recovery |
| DRAINING | receive previous reply/tail; matching Stop if none pending | one END then optional ACK; no new sampling |
| DRAINING | fresh HELLO | barrier/recovery; finish only started old frame within inherited deadline; abandon unstarted DATA/END/ACK |
| TERMINAL | matching Stop if unsolicited END; HELLO | reference same END or new connection |
| FAULT_NO_TERMINAL | fresh HELLO with verified quiescence | old prefix incomplete; never infer clean Stop |
| NEEDS_HELLO | fresh HELLO only | no repeated ordinary reply or acquisition |
| FAULT_HARDWARE | bounded stop-barrier retry or validated power-reset startup | no ACK/new session/START until hardware quiescence is established |

ERROR codes:1 invalid payload,2 bad state,3 stale generation,4 start failed,
5 unsupported type. Reject invalid request without changing acquired samples;
Stop rejection or any integrity/correlation fault requires host recovery.
ERROR.generation is the admitted START attempt's request id once an attempt exists,
including START_FAILED; it is zero before any admitted START in that connection.
This identifies the causal attempt, not successful acquisition. Following failed
START, only the reserved START_FAILED ERROR may finish; subsequent ordinary/Stop
requests get no additional response and require a fresh HELLO.
Malformed or stalled DATA, host queue exhaustion and disk failure preserve the
first local cause, stop accepting new record data, request Stop if framing permits,
and retain verified durable prefix as incomplete. Later normal END cannot erase a
host storage fault. HELLO/reset breaks continuity even if sample numbers coincide.

## 6. Resource and validation gate

Use bounded mutually exclusive storage rather than allocating an extra large ring.
At50000 samples/s:100000 raw bytes/s;4096 bytes buffer about40.96ms, before metadata
and any occupied slots. Current DMA128-sample service interval is2.56ms. These are
arithmetic limits, not measured scheduling margins. Full new RAM/map/stack and DMA
service proof are required. Existing64-byte USB FIFO/two bulk-frame capacity cannot
be assumed sufficient; preserve reserved control capacity and measure sustained TX.

Host display may coalesce20–30 redraws/s and use min/max reduction; raw recording
must not drop samples. Rates/queue capacities are measured configuration limits,
not infinite retention.50kS/s means20us between samples; a faster breakdown peak may
be missed. Neither smooth rendering nor valid CRC establishes ignition bandwidth.

Required shared vectors: exact positive HELLO/CAPS/START/DATA/END/STOP_ACK frames;
negative counts, codes, reserved bits, inconsistent rate/config/session/generation,
duplicate/reordered/missing blocks, u64/u32 limits, empty stream, partial final block,
preview R>0 recording, stop during started frame, unsolicited fault then Stop,
queue/DMA overrun, blocked drain, disconnect/reset and disk-failure precedence.
Reference byte fixtures are in vectors-v0.2-stream.json, reproduced by
tests/stream-contract/make_vectors.py. They are not endpoint conformance tests.
Executable synthetic request-deadline examples are in
tests/stream-contract/request-deadlines/README.md. They exercise a separate test
model, not either production endpoint, and do not cover the full stream state machine.
Implementation and executable state/negative conformance checks remain separate
deliverables. Until they exist and both
critics review the same SHA, this is BLOCKED_EVIDENCE, not an accepted contract.
