# Pre-freeze observations, 2026-10-10 UTC

This is a contemporaneous finding record, not a fabricated command log or a
review verdict. The first run's terminal output exists in the testing task
transcript; it was not redirected to a file before the source changed.

1. Code review found a retained-capture deadlock after consumer acknowledgement
   followed by RELEASE failure/recovery: a second acknowledgement was rejected,
   while start also rejected the retained capture. The author independently
   found and fixed it. A regression covers unknown write, missing ACK timeout
   and explicit disconnect; all permit releasing the exact retained object only
   in recovery, without transmitting another RELEASE.
2. The initial 27-test run failed `test_post_io_zero_write_stall_expires_before_read`:
   write returned 0 at post-I/O time 1000 ms, but phase remained HELLO instead of
   RECOVERY and a read still occurred. The author fixed the post-write check.
3. That initial run also failed the ADC test's overly specific reason assertion:
   the control layer correctly rejected ADC code 4096 before WindowReceiver,
   preserving the earlier fragments with DISCONNECT instead of ADC. The test
   was corrected to respect layered admission, while still checking the exact
   retained samples, no Capture, and no RELEASE. This was a test expectation
   correction, not a production defect.
4. A separate targeted reproduction then showed the same stall invariant was
   missed after read: write returns 0 immediately, read advances the clock by
   1000 ms and returns empty, leaving HELLO at time 1000. The dedicated regression
   checks both zero and positive partial writes at 999 and 1000 ms after read.
   Author was notified before freeze and moved enforcement into the common
   pre/post-I/O tick path; all four boundary cases now pass.

The suite also explicitly tests conservative rejection of an entire mixed
valid/corrupt read batch, because the existing decoder does not expose ordering
between its aggregate diagnostics and decoded frames. Earlier admitted
fragments remain preserved; no attempt is made to infer missing sample values.
