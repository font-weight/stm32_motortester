#!/usr/bin/env python3
"""Verify isolated patch application, existing USB regressions, and ARM object."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--supporting", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    patch_dir = Path(__file__).resolve().parents[2] / "firmware/third_party/usb/patches"
    for root in (args.dependencies, args.supporting, args.firmware, patch_dir):
        root = root.resolve()
        if out == root or root in out.parents:
            parser.error("output must be outside every input tree")
    out.mkdir(parents=True, exist_ok=False)
    commands = []
    env = dict(os.environ, PATH=str(args.toolchain.resolve() / "bin") + os.pathsep + os.environ["PATH"])

    def run(argv, expected=0):
        argv = [str(x) for x in argv]
        result = subprocess.run(argv, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        commands.append({"argv": argv, "exit_code": result.returncode,
                         "expected_exit_code": expected, "output": result.stdout})
        (out / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if result.returncode != expected:
            raise RuntimeError(f"unexpected exit {result.returncode}: {argv}")
        return result.stdout

    source = args.dependencies.resolve() / "vendor/tinyusb/src/device/usbd.c"
    original = source.read_bytes()
    apply = patch_dir / "apply_setup_patch.py"
    patched = out / "usbd.c"
    run(["python3", apply, source, patched])
    run(["python3", apply, source, patched], expected=2)
    bad = out / "wrong-source.c"
    bad.write_bytes(original + b"\n")
    run(["python3", apply, bad, out / "must-not-exist.c"], expected=2)
    assert not (out / "must-not-exist.c").exists()
    patch_copy = out / "tampered-patch"
    shutil.copytree(patch_dir, patch_copy)
    with (patch_copy / "0001-setup-rejection-accounting.patch").open("ab") as file:
        file.write(b"\n")
    run(["python3", patch_copy / "apply_setup_patch.py", source,
         out / "must-not-exist.c"], expected=2)
    assert not (out / "must-not-exist.c").exists()
    deps = out / "dependencies"
    shutil.copytree(args.dependencies.resolve() / "vendor", deps / "vendor")
    for name in ("source-manifest.json", "SHA256SUMS.sources"):
        shutil.copy2(args.dependencies.resolve() / name, deps / name)
    # The original manifest remains provenance of upstream, not a claim that
    # this deliberately patched build copy is byte-identical to upstream.
    (deps / "vendor/tinyusb/src/device/usbd.c").write_bytes(patched.read_bytes())
    harness = out / "supporting"
    harness.mkdir()
    for name in ("build.py", "fake_dcd.c", "fake_dcd.h", "test_core.c", "test_adapter.c"):
        shutil.copy2(args.supporting.resolve() / name, harness / name)
    for name in ("config-buffered", "config-f1"):
        shutil.copytree(args.supporting.resolve() / name, harness / name)
    env.update(USB_DEPENDENCIES=str(deps), USB_FIRMWARE_ROOT=str(args.firmware.resolve()))
    for hardware_fifo in (False, True):
        for sanitize in (False, True):
            for adapter in (False, True):
                flags = (["--hwfifo"] if hardware_fifo else [])
                flags += ["--sanitize"] if sanitize else []
                flags += ["--adapter"] if adapter else []
                run(["python3", harness / "build.py", *flags])
    tc = args.toolchain.resolve()
    compiler = tc / "bin/arm-none-eabi-gcc"
    run([compiler, "--version"])
    flags = ["-std=gnu11", "-Os", "-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft",
             "-ffreestanding", "-ffunction-sections", "-fdata-sections", "-fno-common",
             "-fno-short-enums", "-fstack-usage", "-Wall", "-Wextra", "-Wpedantic",
             "-Wconversion", "-Wsign-conversion", "-DSTM32F103xB",
             "-isystem", str(tc / "include/newlib")]
    flags += ["-I" + str(args.dependencies.resolve() / item) for item in (
        "evidence/config", "vendor/tinyusb/src", "vendor/cmsis-device-f1/Include",
        "vendor/cmsis-core/CMSIS/Core/Include")]
    obj = out / "usbd.o"
    run([compiler, *flags, "-c", patched, "-o", obj])
    for tool, options in (("size", ["-A"]), ("nm", ["-u"]), ("objdump", ["-dr"])):
        text = run([tc / ("bin/arm-none-eabi-" + tool), *options, obj])
        (out / (tool + ".txt")).write_text(text)
    assert source.read_bytes() == original
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in (
        ("original_usbd", source), ("patched_usbd", patched), ("arm_object", obj),
        ("runner", Path(__file__)), ("apply_tool", apply))}
    (out / "summary.json").write_text(json.dumps({"hashes": hashes,
        "existing_usb_executions": 8, "patch_rejection_checks": 3,
        "scope": "native core/adapter tests and ARM object only; no target runtime, IRQ or hardware proof"}, indent=2) + "\n")
    print(json.dumps(hashes, indent=2))


if __name__ == "__main__":
    main()
