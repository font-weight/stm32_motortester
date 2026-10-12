# USB DCD guard inventory

The full actionable inventory is REPORT_RU.md. No production source is changed.

Actual observations (negative controls, not bridge acceptance):

    python3 probes/run.py

Inputs: pristine TinyUSB in /workspace/shared/usb-dependencies/vendor/tinyusb and unchanged fake DCD/config in /workspace/shared/usb-cdc-tests. The runner compiles and executes seven scenarios in buffered and F1 modes, then exact extracted FSDEV SET_ADDRESS with an injected false result at -O0/-O2. It saves argv, exit codes, output, hashes, symbols and effective macros under evidence. Run a COPY of this directory to preserve frozen evidence.

Future root-authored bridge test matrix:

    python3 probes/run_acceptance.py
    python3 probes/run_acceptance.py --driver /absolute/path/to/bridge_test --out results.json

The first command exits 2 with NOT_RUN. The second requires a real bridge harness implementing the CLI/JSON protocol in REPORT_RU.md §9. No implementation or positive result is fabricated. The deconfiguration rows explicitly assume the proposed narrow cleanup-capability policy; they are not an accepted lifecycle contract. Scope changes require explicit criteria updates.

All observed native probes passed their negative-control assertions. Sanitizers, new bridge ARM compilation, concurrency/silicon proof, hardware USB/ADC latency, and full suspend/config0-to-config1 lifecycle validation were not performed.
