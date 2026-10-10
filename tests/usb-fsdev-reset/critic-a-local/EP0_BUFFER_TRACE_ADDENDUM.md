# USB-004 additional buffer-only EP0 trace

The frozen local review report and supporting tests remain unchanged. This adds
an independent supplemental scenario to strengthen interpretation of the original
negative control. The original supporting seed deliberately populated both buffer
and FIFO pointers to test complete field clearing; that is not a normal live
transfer state, as its report already discloses.

I separately initialized buffer-only EP0 transfers with ff=NULL, physical EP0
index 0, no ISO flag, and a different bulk endpoint at physical slot7/EA2. The
model then applies the actual-reset endpoint postcondition, executes the exact
reset/ISR functions, and enters the ISR again. With upstream code, retained RX
produces a false eight-byte logical-EP0 completion and copies slot7 PMA bytes into
the old EP0 buffer; retained TX produces a false EP0-IN completion. No FIFO calls
are involved. With the exact patched code, both directions produce zero stale
completion and zero stale PMA access. Strict C11 and ASan/UBSan both pass all
expected negative/control outcomes (four binaries, two direction scenarios each).

This still uses the supporting register-access model, not real hardware or a
second independently implemented peripheral model. It establishes the false EP0
routing without relying on simultaneous non-null buffer and FIFO fields. It does
not prove the modeled old callback would survive a complete real host/core session.

Portable command after generating the original/candidate supporting model build:

```sh
python3 run_legal_ep0_buffers.py --supporting /path/to/supporting \
  --model-build /path/to/supporting-run-output --out /new/external/output
```

Keep the runner beside test_legal_ep0_buffers.c. The generated model directory is
an input from the separately hash-pinned supporting runner; run that stage first.
Original sources and generated inputs are hashed before and after this check.
Fresh command/receipt evidence is kept separately from all historical runs.

Source SHA-256: adbe1fe0568b743428938c869fe21f0df28f215fafedd612c673f8c31f4c53ff.
Runner SHA-256: fef18fe883462b2a8f4a9e709a0a864fe505801940fd1b6db02167ac157612d0.
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1; no LeakSanitizer claim.

No production change or new defect was found. This adds verification evidence;
final package and immutable binding remain required for the narrow verdict.
