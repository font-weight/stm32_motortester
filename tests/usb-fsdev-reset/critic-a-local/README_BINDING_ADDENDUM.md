# USB-004 final README binding

The local report remains unchanged, SHA-256
c34b6028f9c755844cc7cf7b20365f563ec1e00d0b028372d984de0f0d7846a2.

After that report, the author clarified only the ARM-resource paragraph of
README.md: the patch newly introduces an undefined memset, and the +12-byte
object difference excludes its linked implementation and runtime/stack costs.
I checked the complete wording-only diff and both reproduced nm outputs. This
agrees with the observation already present in my report. No implementation,
script, test, compiler flag or execution evidence changed; no new execution
result is claimed for this documentation edit.

Previous README SHA-256:
1896b001c5e763e0d799f218a615547815a507b4919d05a5bd8de640510b0cde.
Current README SHA-256:
37f957983c41dbfb6b882da896055c530b737cceae93c51f3bc59fe4889c8457.

The package review must bind this current README while preserving the historical
report and its earlier input manifest. Local BLOCKED_EVIDENCE/null remains pending
final package/base and immutable publication checks.
