# Disabled outer terms/transport draft

This successor is isolated from the redirect-fixed evaluated workflow at commit
`a5ca2caecc584ded0d4720bbe1ad1b3bc15660c8` and its original Windows run. That run completed its build and original archive/
inspector checks, then the old checker failed before retention. Its logs remain
preserved; the full v2 terms composition snapshot is under `history/checker-v2/`. It changes no
`host/**` files, recipe, inner candidate ZIP, strict 11 build outputs, app/source
pins, retention helpers or caps. It binds the separately reviewed payload checker
Windows fresh-stat successor (24,668 bytes, SHA-256
`53f9e66afd56c1dd78e1257fd95591f0c15fa4d3c67c6b629d5fc2e79e09d61c`). Neither this draft nor its
optional transport patch has been dispatched or uploaded by this worker.

## Finite changes

1. Bind the frozen launcher r2 and four terms documents, plus its inert test
   harness, from the exact evaluated workflow Git commit. The six reviewed source
   paths start at `scripts/ci/windows-first-test/launcher/`; bytes are checked
   before being written into a separate `terms-input/launcher/` staging directory.
2. Require the existing Windows PowerShell 5.1 interpreter to run the frozen
   `tests/Test-InertLauncher.ps1` harness. Its test fixtures mock choices,
   process launch, console state and paths. The workflow checks all six source
   identities before and after. No execution-policy bypass is used. Actual
   Windows parser/harness acceptance is pending; Linux tests do not supply it.
3. Materialize exactly 16 static support rows: the existing five source archives,
   six app documents, and the five launcher/terms files. The five archive direct
   endpoints, hashes, lengths, redirect whitelist and all streaming limits remain
   unchanged. The six app documents come from the pinned app; the five new bodies
   come only from the separately bound terms input. The 64 MiB per-file and
   128 MiB support caps and 16-row limit remain unchanged.
4. After the existing fresh payload validation, require a read-only byte identity
   receipt for exactly nine pinned supplier-wheel DLL members and two CPython
   source/bundle pairs. The supplier pins reproduce
   `retained-nine-historical-identity.json`, SHA-256
   `e30bc507ab0ce4d1794efd15ff2bfb8286588ab5df8b46db1dde9c4be82d1b5d`.
   That reference is historical attribution. The new receipt measures fresh files,
   joins their manifest and payload inventory entries, and binds the fresh
   manifest/archive/payload reports. The source pair uses exactly
   `C:\hostedtoolcache\windows\Python\3.13.16\x64\VCRUNTIME140.dll` and
   `VCRUNTIME140_1.dll`. Their exact path case is corroborated by the original
   `literal-gate-design/provenance.json`, SHA-256
   `f7e3bceca4d001f80d727e2cd0aa5001b76b3227784080e5c1123d7dd708ac5e`. No DLL is loaded, registered or executed. Signature and
   version metadata explicitly remain `not-collected`. No legal-clearance field
   is introduced. Accepted staging rechecks the 11 bundle members and retains
   `evidence/runtime-identity.json` within the existing cap.
5. After the original archive, manifest, actual EXE, payload and CPython notice
   joins, create `evidence/support/launcher/launch-manifest.json` using the actual
   fresh EXE hash and four exact static document hashes. The fifth document role,
   `pythonLicense`, references the full notice in the manually extracted original
   app. Its bytes remain validated by the existing notice checks; no duplicate
   CPython support row is added. The manifest contains only application/document
   identities, never assent or distribution clearance.

## Recipient layout

The launcher and terms stay under `evidence/support/launcher/`. The recipient
manually extracts the unchanged inner candidate ZIP into that directory's
`app/`, yielding `app/Motortester/Motortester.exe` and
`app/Motortester/licenses/CPython-LICENSE.txt`. The caller performs no extraction,
recipient assent or native app start. The launcher needs the entire original
app tree, not an isolated copied EXE. The existing app `windows-smoke.md` is the
trial guidance; a final physical acceptance checklist remains a separate task.

The successful outer evidence list is 30 rows plus the unchanged 11 build rows.
The launcher harness is evaluated source/test evidence through the workflow
binding and logs; it is not an extra support row. Diagnostic retention remains
explicitly incomplete and text-only, with the runtime JSON added as one bounded
named report. Missing receipts never become synthetic success. The optional
upload remains accepted-kit-only; failed-run diagnostics are not uploaded and
the original job logs remain the remote failure evidence.

## Transport and disposition

`host-windows.local-draft.yml` has an explicit disabled job condition.
`host-windows.terms-transport.patch` is the short patch against the exact a5
redirect-fixed workflow. `host-windows.local-draft.patch` remains the complete
patch against the original archived baseline.

`host-windows.upload-optional.patch` is a separate, disabled one-ZIP transport
proposal: pinned upload-artifact v7 `cf430e030ddbb5b0abf93d22962f4752f3646cd9`,
`archive: true`, one explicit canonical ZIP path, one-day retention, no overwrite,
no hidden files, and missing-file error. Its `PUBLIC_BINARY_UPLOAD_ENABLED` switch
is false pending exact distribution and transport integration review. Owner
permission is not adjudicated by this worker. No new token, release, or
contents-write permission is requested. Source/notice coverage and distribution
disposition remain external review responsibilities, not JSON approval flags.

## Reproduce the inert tests

Set these explicit local input paths; their bodies are checked against the
published pins. The directories need not use this workspace's private layout:

- `CALLER_BASE_WORKFLOW`: original workflow, SHA-256 `7341772216fa98fc98946e466cf36038a66dfec15977b3118d76e28109a998b6`
- `CALLER_TERMS_BASE_WORKFLOW`: redirect-fixed enabled workflow, SHA-256 `6408b194dbf3b1dc23b2c4e66aa0780bb8be412c24014c0fb59a830b88328127`
- `CALLER_TEST_HELPERS`: the exact retention49 helper directory
- `CALLER_TEST_APP_SOURCE`: separate exact app dcaf checkout (guidance/literal notices)
- `CALLER_TEST_BASELINE`: preserved caller r2 plus `history/r1/` fixture files
- `CALLER_TEST_OLD_RETAINER`: original retain.py, SHA-256 `9e51a4bc9d8cde8ad52c3f286dfff1f262eeb322d96b10f7f0263fc811c11dd7`
- `CALLER_TEST_CHECKER`: Windows fresh-stat checker successor, SHA-256 `53f9e66afd56c1dd78e1257fd95591f0c15fa4d3c67c6b629d5fc2e79e09d61c`
- `CALLER_TEST_LAUNCHER_SOURCE`: exact frozen r2 launcher source directory
- `CALLER_TEST_RUNTIME_REFERENCE`: the pinned historical nine-member JSON above
- `CALLER_TEST_ORIGINAL_WORKFLOW`: pre-redirect-fix enabled workflow, SHA-256 `3b502696c31bccc3504b519f921551f453d0ca2a995b55b40c0bfcbcc06bb01a`

Run `python -B build_workflow_draft.py`, then each `test_*.py` with `python -B`
and `python -B -O`. PyYAML is needed only by the static/materializer tests.

The caller fixtures intentionally use inert text, including DLL-named text, and
patch NOTICE_HASH and WHEEL_RUNTIME_PINS only inside the in-memory test module.
The real production constants are unchanged. A real unpatched CLI negative test
rejects the inert fixture. The positive CLI test explicitly applies the same
fixture-only patches, while retaining the real isolated retention/ZIP helpers.
The production nine-member constants are independently matched to the pinned
historical JSON before fixture substitution. Archive download tests use bounded
in-memory responses and prohibit networking/process execution. Windows path tests
model the real unchanged strict retainer guard using PureWindowsPath; they do not
claim Windows execution. No Linux result proves fresh Windows native behavior,
recipient acceptance, delivery, or legal sufficiency.

## Exact-EXE Auto/no-edge coverage gap

The current dcaf CLI provides only `--serial-import-smoke OUTPUT.json`,
`--screenshot OUTPUT.png`, and `--recording-smoke OUTPUT.json`
(`host/packaging/launch_gui.py:189–208`). The frozen recording smoke fixes Single
rising/falling and Normal/no-edge cases (`recording_smoke.py:344–468`), then a
Normal/pre0 run. It does not establish Auto timeout behavior.

The existing GUI does expose the separate owner rehearsal: choose Emulator,
Auto, pre0, 1000 ms, and turn off “Событие запуска в эмуляторе”; then Record,
Stop, wait for Save to finish, and freshly open the saved `.mtrec`.
`gui_acquisition.py:136–190,263–281` exposes these controls;
`acquisition.py:334–346` suppresses the simulated edge when disabled; and
`device_emulator.py:239–244` implements the timeout. This confirms route
availability from source, not an executed result. The delivered, SHA-bound EXE
still needs the separately reviewed interactive rehearsal proving 2048 samples,
`auto_timeout`, and absent trigger index/marker before any real COM use. No new
host change or external UI-driving framework is included in this draft.
