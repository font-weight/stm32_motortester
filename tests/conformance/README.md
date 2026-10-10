# Independent protocol conformance

Scope: native software tests of `mt_wire`, `mt_stream`, and `mt_command` only.
This suite neither awards a review score nor accepts the shared wire contract.
Critic-A and Critic-B retain the requirements in `docs/quality-gate.md`.

## Provenance

- Implementation author: Dot A. Test author: independently assigned conformance
  worker; no implementation files edited and no author's test files read or reused.
- Contract: [wire v0.1 at PR4](https://github.com/font-weight/stm32_motortester/blob/124b3f32e1e7f7282bce283baa4c9406d4d72950/protocol/wire-v0.1.md).
- Wire implementation: `124b3f32e1e7f7282bce283baa4c9406d4d72950`.
- Stream/command API and implementation: `0f285510c1b9b6bd21bb7b60ae6098f7584ae71a`.
  GitHub blob identities confirm wire `.c/.h` are unchanged at that PR8 commit.
- Inputs were materialized through the GitHub connector. `run.py` verifies their
  exact Git blob hashes before compiling. This is source-snapshot evidence, not
  a claim that a clean full-repository checkout or target firmware was built.
- `generate_vectors.py` uses Python `struct`, `zlib.crc32`, and a zero-separated
  run COBS algorithm. It never invokes or imports production code. Literal CRC
  `123456789 -> cbf43926` and COBS anchors check the fixture generator.
- `vectors.json` is a portable corpus; `vectors.h` is its generated C equivalent.
  There are 46 complete byte fixtures: seven host commands plus 39 boundary
  patterns. The file includes raw bytes, CRC, and delimited COBS bytes.
- The C differential oracle uses the non-reflected `0x04c11db7` polynomial with
  explicit input/output reflection, a different computation from production's
  reflected right-shift implementation. 541 Python/zlib prefix CRCs check it.
- Fixtures and expectations derive from the written contract; implementation was
  read to establish API scope. Pseudorandom cases use seed `0x6d747031`.

## Run

From a checkout containing these tests and the pinned source files:

```sh
python3 tests/conformance/run.py --output-dir /tmp/mt-conformance-evidence
```

The runner uses Python's standard library and an installed C11 compiler. No
package installation, network access, hardware, USB library, or host code is
required. It runs strict C11 with warnings-as-errors at `-O2`, then ASan+UBSan at
`-O1`. Both return nonzero on failure. Assertions remain active with `NDEBUG`.

To test a source snapshot elsewhere, pass `--source-root /path/to/repository`.
`--cc` selects the installed compiler. Source drift fails by default; the explicit
`--allow-source-drift` option records changed hashes and marks the run as not
certifying the pinned commits. `--no-sanitizers` records sanitizers as NOT RUN.

`ASAN_OPTIONS=detect_leaks=0:halt_on_error=1:abort_on_error=1` and
`UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1` are recorded in the report.
LeakSanitizer is intentionally not claimed: the available execution environment
has a reported ptrace restriction. Address and undefined-behavior checks run.

Outputs are `run.log`, `report.json`, and two executables in the requested output
directory. The JSON records complete commands, UTC times, compiler/platform,
source Git blob/SHA-256 identities, test SHA-256 identities, and stage exit codes.
Regenerate fixtures with `python3 tests/conformance/generate_vectors.py`; check
them without writing with the same command followed by `--check`.

## Case matrix

| Area | Independent checks |
|---|---|
| CRC/COBS | Known check value, empty CRC, 541 prefix values, literal COBS anchors, zero/nonzero/ramp payloads including 253/254/255 and maximum length, exact encoded bytes |
| Byte fields | Asymmetric little-endian IDs, zero/max identifiers, delimiter exclusion/inclusion, type transparency, payload equality, untouched input and buffer canaries |
| Length/corruption | Every insufficient encode capacity, empty/short/oversized decode, truncated COBS blocks, embedded zero, every truncation of CONFIG, wrong payload lengths, single-bit changes throughout a raw CONFIG frame |
| Validation precedence | Length before CRC; CRC before version/reserved; both version bytes across all unsupported values; all nonzero values in every header reserved byte; version before reserved |
| Differential/robustness | 4,096 independent generated messages including every payload length 0–512; 10,000 bounded malformed inputs with canaries and unchanged outputs on failure |
| Stream reads | Every two-part split of a maximum payload frame; chunk sizes 1/2/3/7/63/64/65/127/255/544; 200 deterministic random chunk schedules; seven coalesced commands; empty separators |
| Stream recovery | Framing/CRC rejection then immediately valid frame, exact storage capacity, overflow on following byte, prolonged discard, zero resync, explicit reset, NULL arguments |
| Time | Interbyte 999/1000 ms and frame 2999/3000 ms boundaries, poll versus feed, late zero and nonzero consumption, successful below-boundary frame, uint32 clock rollover |
| Command syntax | All seven command layouts; all alternate lengths 0–513 plus 65535; every type byte; named device directions; each CONFIG reserved byte with every nonzero value; zero nonce and all 64 one-bit nonces |
| Parse/semantic separation | Invalid session/order intentionally parsed without enforcement; zero/max IDs preserved; semantic-invalid but syntactically valid CONFIG parsed unchanged |
| CONFIG support | Full Cartesian product of all legal capability masks, modes, sources, edges and pretrigger choices; unknown capability bits; all values of every 8/16-bit configuration field; timeout boundaries and mode constraints |
| Output invariants | Guards around outputs, no changed prior output on errors/non-message events, no input mutation; unused bytes after successful output are deliberately unspecified |

The three-million-plus assertion count includes per-byte guards and repeated
invariants. It is not a count of independent scenarios or a quality score.

## Observed results and limits

The checked-in `evidence/native-20261010/` records actual strict and sanitizer
runs. Read its report for the exact test/source hashes; those results apply only
to those bytes. No tested defect was found in this bounded scope.

`evidence/preflight-20261010/` preserves an earlier source-import failure: the
first file materialization added a trailing newline, and the Git blob guard
stopped before compilation. Exact connector-returned bytes replaced that local
snapshot. No implementation change or failed execution is hidden by the final run.

Not checked: target ARM compilation/ABI/stack, native USB/CDC, actual RAM/Flash
budget, physical sampling/timing, Windows integration, device-payload semantics,
session negotiation and sequence/request state, duplicate handling, dispatch,
Stop/rearm/terminal ordering, window assembly, file round-trip, or 20-window
end-to-end acceptance. Parser success does not authorize execution of a command.
Unknown reserved message-type slots 136–143 are treated as unknown by the current
API; only named device message types in the payload table are direction-tested.
They must be clarified if a future contract reserves direction for those slots.

No repository publication, merge, readiness level, hardware result, or critic
approval follows from a passing native run.
