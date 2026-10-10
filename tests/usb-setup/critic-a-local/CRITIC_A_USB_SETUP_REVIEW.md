# Critic-A: USB-003 SETUP rejection accounting, local review

Repository: font-weight/stm32_motortester. Task: issue #55.
Reviewer: the existing independent Critic-A, not the implementation author.
Review date: 2026-10-10. Published subject/base: not yet assigned for USB-003.
Verdict: BLOCKED_EVIDENCE. Score: null. This report clears the inspected local
implementation and current package for preparation of a draft, subject to final
base/package reconciliation and immutable readback. It does not accept USB lifecycle
integration, target synchronization, full firmware or physical USB.

## Subject and exact identities

The task is a narrow TinyUSB 0.21.0 accounting correction under sequential,
non-overlapping, non-nested producer and consumer calls. The original issue
explicitly excludes IRQ synchronization and the full epoch/ingress bridge.

- Upstream commit: dae3f9a366bfcddbf9dcf1b48d7500286a849539.
- Original src/device/usbd.c SHA-256:
  38de54351d81878e6543f2b022d0c39f45d28c8f0888491796398dec45e727be.
  I independently retrieved the official upstream file; its Git blob is
  c4c418cb5e03a76deeab566ddac318eb903dba66 and all 58,890 bytes match the input.
- Patched build file SHA-256:
  187144971b52e77385809500bd7401b46c3c332acda5f6221a1b45e440b8ddd0.
- Patch: 51492cd96f4f9f8dd0699fe915f8c157c866f04cdc129e4c7c734e21f49b0f37.
- Apply tool: 28b1672b247a71d149b47d631659df9e09c9d761ea413bbedbf1c571658bb189.
- Final author runner: 06b9b9237295786ddbdc152e2040a42e57e9fc6632685dbdfe00698e2483d57b.
- Final root README: c15c94cf1d52d566411f07dd37a2f6dfee9c87eda27f1dab1b94a39cb3d910d5.
- Supporting test: 069d5996c665934db088c5a9cf0d70040efe7cf04b7beb6c5bea2e431cccc5dc.
- Supporting runner: 1565549e4d00124efea8105ba376bc50295195b8da1bff4b31252d8f68e8addc.
- Supporting report: e5b9aa5defd052acce6e898fb6c4460f39da0aca7cce7b84db7a9e17c52c2a97.

## Source reasoning and findings

No remaining demonstrated defect in the stated sequential accounting scope.

The patch subtracts only the increment belonging to a rejected SETUP enqueue.
It leaves the successful increment/enqueue/hook ordering and accepted-SETUP
supersession unchanged. It does not clear the counter on BUS_RESET; doing so
would miscount accepted SETUP events already queued after that reset. With the
fixed queue capacity, the sequential pending accepted count stays bounded.
Repeated rejected attempts no longer leave a phantom queued SETUP.

This is not a repair of upstream cross-context read/modify/write races.
The emitted ARM decrement remains load/subtract/store. Serialized producers and
a non-reentrant consumer alone do not establish non-overlap between those two
contexts. The final README explicitly states the stronger tested assumption and
the missing target critical section. A rejected newest SETUP can still leave an
older accepted control request executable; a fail-closed overflow/epoch policy
must prevent unsafe stale operations in a future integration.

Closed P2-USB003-1, runner output isolation: the earlier check_patch.py permitted
an output below an input tree, risking input pollution and recursive copy.
The author now resolves and rejects equal/nested destinations for dependencies,
supporting, firmware and patch inputs before mkdir. I ran 12 equal, nested and
symlink-alias cases against the final runner: each was rejected before creation.
The original finding was by inspection, not a claimed saved destructive run.

Closed P2-USB003-2, scope wording: the final README explicitly distinguishes
sequential producer/consumer evidence from merely producer serialization and
consumer non-reentrancy. No code or test was changed to obtain this clarification.

## Evidence personally reproduced

Environment: Python 3.12.14, GCC 14.2.0, Linux native fake DCD with actual TinyUSB
core/FIFO/CDC, OS_NONE, queue depth 16 and one-event task budget. Both buffered
and STM32F1 dedicated-HWFIFO configurations were exercised. ASan and UBSan passed
with detect_leaks=0; LeakSanitizer is unavailable under the recorded ptrace setup.

1. Four complete supporting matrices, 30 executions each:
   upstream native and sanitized each reproduce 16 specific EP0-response failures
   and 14 passing controls; patched native and sanitized each pass all 30.
   The upstream runner exit zero means the expected negative profile matched.
   It does not mean the unmodified implementation passed.
2. The scenarios include 1, 255, 256, 257, 511, 512, 513 and 1025 rejected SETUPs;
   accepted supersession; a full accepted queue; mixed accepted/rejected events;
   and accepted requests after queued BUS_RESET. Multiples of 256 are deliberate
   uint8 wrap controls that can hide the upstream defect. Public callback/EP0
   behavior, not direct private-counter writes, is the test oracle.
3. All eight unchanged USB-001 core/adapter regressions passed against a separate
   patched dependency copy. The twelve 11,520-byte successful-stream captures
   remain identical, SHA-256 b903eaa836ac3f46be6aeb451c95d79066871e12dec9d264f1cf4836de325cf7.
4. My independent apply-tool test ran eleven invocations, including source/patch/
   expected-result corruption, existing and aliased outputs, symlink cases and
   a post-patch/pre-exclusive-create competing writer. Rejection preserves inputs
   and existing output; normal application gives the exact pinned result.
5. The final author runner's three rejection tests and its ARM compilation passed.
   Cortex-M3 -mthumb -Os GNU11 object SHA-256:
   da467c706a64bd3463194649346ce1ca4060f11e4cde4174fbb5e7b03be28d2d.
   Object text plus rodata is 4,088 bytes, data 29, bss 310. No warnings in this
   patched translation unit. Disassembly confirms the new decrement and also
   confirms that it is not atomic. No complete ELF, complete stack bound, ARM
   execution or board measurement is claimed.
6. I repeated the package's complete documented command sequence in a relocated
   assembly: eight regressions plus the four 30-execution matrices and ARM build.
   All expected outcomes matched and no recorded input changed.

The supporting worker authored its real-core harness; I inspected and independently
executed it, rather than claiming to have authored it. My additional Python tests
exercise patch application and runner isolation. The implementation was not edited
by this reviewer.

## Reproduction and artifacts

The reviewed package provides explicit full-checkout commands in
tests/usb-setup/PACKAGE_RU.md. Set ROOT to the checkout, TOOLCHAIN to the installed
ARM toolchain's usr directory, and choose a new output directory. No downloads
or tool installation are performed. check_patch.py accepts explicit dependency,
supporting, firmware, toolchain and output paths. The supporting run.py accepts
--dependencies, --harness, --out, --usbd, --expect upstream and --sanitize.

This review's artifact root is critic-a-usb-setup-review. Evidence includes:
upstream-remote-binding.json; patch-tool-results.json; output-separation-results.json;
the four evidence-{upstream,fixed}-{native,sanitized} directories and their complete
logs; author-reproduction/summary.json and commands; successful-stream-equality.json;
package-verification.json; package-run/ with the repeated complete sequence.
Independent supplemental sources are test_patch_tool.py and test_output_separation.py.
The source/evidence manifest accompanies this report.

## Package inspection and remaining binding

The inspected additive package has 49 text files, 628,193 bytes. Its external
additions-manifest.json SHA-256 is
6dddb986a145755dc16daffe67bafc615449df96eee7ed9639d9f7bfcbc8a799.
I verified exact path, size and SHA-256 equality for every listed file. Vendor
sources remain unchanged. Omitted generated objects, executables, patched build
file and repeated raw captures are disclosed and regenerated by the commands.
The original full supporting checksum list is explicitly historical; it is not
misrepresented as a runnable manifest for the selected text-only package.

My relocated assembly used the 202 USB-001 additions plus 32 required inherited
firmware inputs and these 49 additions. It is not a claim of having executed a
future immutable full checkout. Publication is now intended to stack on USB-002;
the current PACKAGE_RU.md still describes the earlier prospective USB-001 base.
That metadata/documentation must be reconciled with the actual base and any added
review artifacts before final package clearance and exact-commit binding.

C1 correctness, C2 failure handling, C3 verification, C4 maintainability and
C5 reproducibility have the concrete evidence above. Numeric scores remain null
until the final intended package/base and immutable subject are verified.
The broader missing ingress/epoch/IRQ serialization and hardware requirements
remain open regardless of a future narrow USB-003 verdict. No merge or readiness
claim is authorized by this local report.
