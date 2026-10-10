#!/usr/bin/env python3
"""Reproduce metadata tests, mutation sensitivity and ARM object checks.
No original firmware/host or upstream files are edited. No full firmware link.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "inputs"
EVIDENCE = ROOT / "evidence"
SOURCE = INPUT / "mt_usb_epoch.c"
HEADER = INPUT / "mt_usb_epoch.h"
TEST = ROOT / "tests/epoch_independent.c"
TCROOT = Path(os.environ.get("ARM_TOOLCHAIN_ROOT", "/workspace/shared/arm-resource-evidence/toolchain/root/usr"))
EVIDENCE.mkdir(exist_ok=True)
COMMANDS = []

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(args, name, cwd=EVIDENCE, accepted=(0,), env=None):
    start = time.monotonic()
    p = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, text=True, env=env)
    result = dict(name=name, argv=[str(x) for x in args], cwd=str(cwd), exit_code=p.returncode,
                  seconds=round(time.monotonic()-start, 4), stdout=p.stdout, stderr=p.stderr)
    COMMANDS.append(result)
    (EVIDENCE / "commands.json").write_text(json.dumps(COMMANDS, indent=2) + "\n")
    (EVIDENCE / (name + ".log")).write_text("$ " + " ".join(str(x) for x in args) + "\n" + p.stdout + p.stderr + f"\nexit={p.returncode}\n")
    if p.returncode not in accepted:
        raise RuntimeError(f"{name} failed: {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p

flags = ["-std=c11", "-pedantic-errors", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wsign-conversion", "-Wshadow", "-Wstrict-prototypes", "-Wmissing-prototypes"]
run(["gcc", "--version"], "native-toolchain")
run(["gcc", *flags, "-O2", "-g", "-I"+str(INPUT), TEST, SOURCE, "-o", EVIDENCE / "test-strict"], "strict-build")
run([EVIDENCE / "test-strict"], "strict-run")
run(["gcc", *flags, "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-sanitize-recover=all", "-no-pie", "-I"+str(INPUT), TEST, SOURCE, "-o", EVIDENCE / "test-sanitize"], "sanitizer-build")
env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1", UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
run([EVIDENCE / "test-sanitize"], "sanitizer-run", env=env)
coverage = EVIDENCE / "coverage"
coverage.mkdir(exist_ok=True)
for f in coverage.glob("*.gcda"):
    f.unlink()
run(["gcc", *flags, "-O0", "-g", "--coverage", "-I"+str(INPUT), "-c", SOURCE, "-o", coverage / "mt_usb_epoch.o"], "coverage-source-build", coverage)
run(["gcc", *flags, "-O0", "-g", "--coverage", "-I"+str(INPUT), TEST, coverage / "mt_usb_epoch.o", "-o", coverage / "test-coverage"], "coverage-test-build", coverage)
run([coverage / "test-coverage"], "coverage-run", coverage)
run(["gcov", "-b", "-c", "mt_usb_epoch.gcno"], "coverage-gcov", coverage)

mutations = {
 "ignore_stale_identity": ("token == s->current", "s->current != 0"),
 "ignore_cleanup": ("s->cleaned != token", "0"),
 "ignore_queue_loss": ("s->faults |= MT_USB_EPOCH_QUEUE_LOSS;", "s->faults |= 0u;"),
 "skip_processed_receipt": ("return s->processed_reset == token && !s->pending_soft;", "return !s->pending_soft;"),
 "skip_config_receipt": ("&& s->configured == token", ""),
 "allow_old_pause_ack": ("serial != s->pending_soft", "!s->pending_soft"),
 "allow_hard_wrap": ("s->current == UINT32_MAX", "0"),
 "allow_diagnostic_wrap": ("if (*value != UINT32_MAX) ++*value;", "++*value;"),
 "dispatch_during_pause": ("return s->processed_reset == token && !s->pending_soft;", "return s->processed_reset == token;"),
 "skip_enqueue_receipt": ("s->queued_reset == token", "1"),
}
mut_dir = EVIDENCE / "mutants"
mut_dir.mkdir(exist_ok=True)
mut_results = []
text = SOURCE.read_text()
for name, (old, new) in mutations.items():
    assert text.count(old) == 1, (name, text.count(old))
    src = mut_dir / (name + ".c")
    src.write_text(text.replace(old, new))
    binary = mut_dir / name
    run(["gcc", *flags, "-O2", "-I"+str(INPUT), TEST, src, "-o", binary], "mutation-"+name+"-build")
    p = run([binary, "--directed"], "mutation-"+name+"-run", accepted=(1,))
    assert "FAIL " in p.stderr
    mut_results.append(dict(name=name, detected=True, failure=p.stderr.strip(), source_sha256=sha(src)))
(EVIDENCE / "mutation-results.json").write_text(json.dumps(mut_results, indent=2) + "\n")

arm = TCROOT / "bin/arm-none-eabi-gcc"
run([arm, "--version"], "arm-toolchain")
armflags = [*flags, "-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft", "-O2", "-ffreestanding", "-fno-common", "-fno-lto", "-fstack-usage", "-I"+str(INPUT), "-isystem", TCROOT / "include/newlib"]
obj = EVIDENCE / "mt_usb_epoch.arm.o"
run([arm, *armflags, "-c", SOURCE, "-o", obj], "arm-source-build")
run([arm, *armflags, "-c", ROOT / "tests/abi_probe.c", "-o", EVIDENCE / "abi_probe.arm.o"], "arm-abi-build")
run([TCROOT / "bin/arm-none-eabi-readelf", "-h", "-A", obj], "arm-attributes")
run([TCROOT / "bin/arm-none-eabi-objdump", "-dr", obj], "arm-disassembly")
run([TCROOT / "bin/arm-none-eabi-size", "-A", obj], "arm-size")
run([TCROOT / "bin/arm-none-eabi-nm", "-S", "--size-sort", obj], "arm-symbols")
u = run([TCROOT / "bin/arm-none-eabi-nm", "-u", obj], "arm-undefined")
assert u.stdout.split() == ["U", "memset"], u.stdout
# This object requires the usual platform memset; no libatomic/locking helpers.
package_dir = TCROOT.parents[1] / "packages"
package_sums = TCROOT.parents[1] / "SHA256SUMS.packages"
run(["sha256sum", "-c", package_sums], "arm-package-hashes", package_dir)
manifest_paths = [SOURCE, HEADER, TEST, ROOT / "tests/abi_probe.c", Path(__file__).resolve(), obj,
                  EVIDENCE / "test-strict", EVIDENCE / "test-sanitize", INPUT / "issue54.json",
                  INPUT / "reset-barrier-REPORT_RU.md", INPUT / "reference-provenance.json", arm]
manifest = [{"path": str(p), "sha256": sha(p)} for p in manifest_paths]
(EVIDENCE / "input-output-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("PASS: native strict, ASan/UBSan, coverage, 10/10 safety mutants detected, ARM object/ABI, package hashes")
