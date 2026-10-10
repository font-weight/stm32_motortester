#!/usr/bin/env python3
"""Compare complete original/patched F103 DCD ARM objects, without execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dep, tc, out = args.dependencies.resolve(), args.toolchain.resolve(), args.output.resolve()
    root = Path(__file__).resolve().parents[2]
    patches = root / "firmware/third_party/usb/patches"
    for input_root in (dep, tc, patches):
        if out == input_root or input_root in out.parents:
            parser.error("output must be outside every input tree")
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PATH=str(tc / "bin") + os.pathsep + os.environ["PATH"])
    commands = []

    def run(argv, expected=0, cwd=None):
        argv = [str(x) for x in argv]
        result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        commands.append({"argv": argv, "cwd": str(cwd) if cwd else None,
                         "exit_code": result.returncode, "expected_exit_code": expected,
                         "output": result.stdout})
        (out / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if result.returncode != expected:
            raise RuntimeError(f"unexpected exit {result.returncode}: {argv}")
        return result.stdout

    run(["sha256sum", "-c", "SHA256SUMS.sources"], cwd=dep)
    manifest = json.loads((patches / "f103-reset-cleanup.json").read_text())
    source = dep / "vendor/tinyusb" / manifest["source_path"]
    patched = out / "dcd_stm32_fsdev.c"
    apply = patches / "apply_f103_reset_patch.py"
    run(["python3", apply, source, patched])
    run(["python3", apply, source, patched], expected=2)
    wrong = out / "wrong-source.c"
    wrong.write_bytes(source.read_bytes() + b"\n")
    run(["python3", apply, wrong, out / "must-not-exist.c"], expected=2)
    assert not (out / "must-not-exist.c").exists()
    flags = ["-std=gnu11", "-Os", "-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft",
             "-ffreestanding", "-ffunction-sections", "-fdata-sections", "-fno-common",
             "-fno-short-enums", "-fstack-usage", "-Wall", "-Wextra", "-Wpedantic",
             "-Wconversion", "-Wsign-conversion", "-DSTM32F103xB",
             "-isystem", str(tc / "include/newlib")]
    flags += ["-I" + str(dep / path) for path in (
        "evidence/config", "vendor/tinyusb/src", "vendor/cmsis-device-f1/Include",
        "vendor/cmsis-core/CMSIS/Core/Include",
        "vendor/tinyusb/src/portable/st/stm32_fsdev")]
    compiler = tc / "bin/arm-none-eabi-gcc"
    run([compiler, "--version"])
    inputs = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in dep.rglob("*") if path.is_file() and
              ("vendor" in path.relative_to(dep).parts or
               path == dep / "evidence/config/tusb_config.h")}
    objects = {}
    for name, path in (("upstream", source), ("patched", patched)):
        obj = out / (name + ".o")
        run([compiler, *flags, "-c", path, "-o", obj])
        objects[name] = hashlib.sha256(obj.read_bytes()).hexdigest()
        for tool, options in (("size", ["-A"]), ("nm", ["-u"]), ("objdump", ["-dr"])):
            data = run([tc / ("bin/arm-none-eabi-" + tool), *options, obj])
            (out / (name + "-" + tool + ".txt")).write_text(data)
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha for path, sha in inputs.items())
    (out / "input-manifest.json").write_text(json.dumps(inputs, indent=2) + "\n")
    result = {"object_sha256": objects, "patch": manifest,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "complete F103 DCD relocatable ARM objects only; no ARM execution or peripheral test"}
    (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
