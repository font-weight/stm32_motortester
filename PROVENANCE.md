# Run18 retained metadata: exact hash-verified serializations

This is a data-only evidence snapshot for the existing MotorTester project. It is not an application source tree, a workflow, a downloadable Windows application, or a new execution result. It contains only the four metadata files listed below and this note. Do not merge its tree into an application branch.

Primary execution: [GitHub Actions run 38572185383](https://github.com/font-weight/stm32_motortester/actions/runs/38572185383), job 115879974929, 2026-10-14. The selected application was 244b0786a18fed19a9e708bb815150475ddd193f, tree 0c0e61fb8aadd4a4380e2d7e0b8818e4c9da0d5e. Selected consumer 9a7bc49c48daef9ea26c1b638ccea0f22e021158; actual evaluated workflow commit 193a789507334661f4a5bd8f881b8c01b07e59f7. This snapshot does not relabel that historical execution as a later main commit.

The four files were recovered from the public timestamped job log. They are the uniquely hash-verified original byte serializations reconstructed from that log; they were not downloaded from a retained Actions artifact. The retained primary log is 2,105,333 bytes, SHA-256 80b517c85dba0f505ff87e570db7f7b550c5f1744419b411db51a79828ca7997. Recovery removed each validated GitHub timestamp prefix and considered LF/CRLF, final-newline and UTF-8-BOM variants. Only the unique serialization matching the separately recorded hash was accepted. No JSON fields were edited or reserialized for publication.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| build-manifest.json | 82537 | 999630dfd15c0eccd9197756731e19b4af44ad7eec49e31abe9ed32180a7d73e |
| provenance.json | 388597 | f7e3bceca4d001f80d727e2cd0aa5001b76b3227784080e5c1123d7dd708ac5e |
| archive-verification.json | 5420 | 53bbbb3bccc6bb39cc210af482fbd22d1ce742cb242a1ce93168cf2d29273557 |
| SHA256SUMS.txt | 801 | d65262f7e11a58f2d6cf922ba1ca1c81d9a97b17acedc1dd92c9a6737be0bad1 |

Total metadata bytes: 477355. This total does not count this note, copies, rereads, reports or any other auxiliary-budget charge. It does not justify increasing any existing reader limit or replacing actual accounting with a size estimate.

The build-manifest and checksum-list hashes are bound by the original archive-verification receipt. The provenance serialization is bound by the explicit PROVENANCE_RECEIPT length/hash header. The original archive-verification serialization is additionally bound by the native-observation receipt's input hash; its separately retained normalized-log form is not substituted here. CRLF is retained in the manifest, archive receipt and checksum list; provenance retains LF.

These metadata record successful in-run checks. The run retained zero Actions artifacts. The original Windows EXE, binary ZIP, recording archive and spool bytes remain unavailable in this snapshot; their reported hashes do not prove present-day access to those bytes. Reconstructed source ZIPs are separately qualified and are not included here. This snapshot does not establish native-origin authenticity, corresponding-source completeness, replacement, distribution clearance, physical hardware acceptance or Windows 10 acceptance. Existing false/unknown fields remain unchanged.

Purpose: permit a narrowly bounded, exact-input L1/L5 metadata-join review and reproducible retention, without parsing a large log again or rehashing a full ZIP under a small auxiliary-metadata budget. Full Windows orchestration, source-pool admission and any actual replacement execution require their own exact reviewed inputs and evidence.
