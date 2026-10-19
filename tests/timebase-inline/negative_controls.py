#!/usr/bin/env python3
"""Prove the trace gate rejects altered accesses; write only into OUT."""
import argparse
import hashlib
import pathlib
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("out", type=pathlib.Path)
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[2]
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
source_path = root / "firmware/platform/mt_timebase.c"
original = source_path.read_bytes()
source = original.decode()
variants = {
    "omitted-read": (
        "read_reg(t, TIM_CR2) == 0u &&\n        ", ""
    ),
    "reordered-read": (
        "read_reg(t, TIM_CR2) == 0u &&\n        read_reg(t, TIM_SMCR) == 0u",
        "read_reg(t, TIM_SMCR) == 0u &&\n        read_reg(t, TIM_CR2) == 0u"
    ),
    "duplicated-stop-read": (
        "t->owns_timer = 0;",
        "(void)read_reg(t, TIM_CR1);\n        t->owns_timer = 0;"
    ),
    "late-failure-short-circuit": (
        "return (read_reg(t, RCC_APB1ENR) & 1u) && !(read_reg(t, RCC_APB1RSTR) & 1u);",
        "uint32_t enabled = read_reg(t, RCC_APB1ENR) & 1u;\n"
        "    if (!enabled) { (void)read_reg(t, RCC_APB1RSTR); return 0; }\n"
        "    return !(read_reg(t, RCC_APB1RSTR) & 1u);"
    )
}
flags = "-std=c11 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Werror -O2".split()
with (out / "negative-controls.log").open("w") as log:
    log.write("source SHA-256 " + hashlib.sha256(original).hexdigest() + "\n")
    for name, (before, after) in variants.items():
        assert source.count(before) == 1, (name, "source anchor changed")
        mutant = out / (name + ".c")
        mutant.write_text(source.replace(before, after))
        executable = out / (name + "-mutant")
        command = ["cc", *flags, "-I", str(root / "firmware/platform"),
                   str(mutant), str(root / "tests/timebase-inline/test_trace.c"),
                   "-o", str(executable)]
        log.write("compile: " + " ".join(command) + "\n")
        subprocess.run(command, check=True)
        command = [str(executable), str(out / (name + ".trace"))]
        log.write("run: " + " ".join(command) + "\n")
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=30)
        assert result.returncode != 0 and "Assertion" in result.stdout, (name, result)
        log.write(f"{name}: rejected (exit {result.returncode})\n{result.stdout}\n")
        print(f"PASS negative control {name}: rejected by assertion")
assert source_path.read_bytes() == original, "production source changed"
