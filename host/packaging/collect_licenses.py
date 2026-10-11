"""Copy installed notices for review; not an automatic license-compliance audit."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    inventory = []
    keywords = ("license", "licence", "copying", "notice", "copyright")
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        copied = []
        for entry in dist.files or ():
            relative = Path(str(entry))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            if not any(any(word in part.lower() for word in keywords) for part in relative.parts):
                continue
            source = Path(dist.locate_file(entry))
            if not source.is_file():
                continue
            target = destination / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target.relative_to(destination).as_posix())
        inventory.append({
            "name": name,
            "version": dist.version,
            "license_expression": dist.metadata.get("License-Expression"),
            "license_metadata": dist.metadata.get("License"),
            "copied_notices": copied,
            "manual_review_required": True,
        })
    runtime_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not runtime_license.is_file():
        raise RuntimeError(f"CPython LICENSE.txt missing: {runtime_license}")
    shutil.copy2(runtime_license, destination / "CPython-LICENSE.txt")
    (destination / "inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    collect(parser.parse_args().output)
