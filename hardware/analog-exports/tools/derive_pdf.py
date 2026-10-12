"""Remove the known KiCad JSInit from two exact draft exports; never a general sanitizer."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import pypdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

PINS = {
    "option-a-dc.pdf": "d8af3c98e5bc6b77c8eedae69bc22f13302768f8048e3bdabfe1809ce2130103",
    "option-b-ac.pdf": "44f29c141a54adee2cf514b3feee061e1549c6fd6549ca4b4c51263c631ef074",
}
JS_PIN = "78b1ed3fb0d019f30f03f9e111323344804c1d093753a8553ebc74ec96959236"
FORBIDDEN = {"/JavaScript", "/JS", "/OpenAction", "/AA", "/EmbeddedFiles", "/EF", "/AcroForm", "/XFA", "/RichMediaContent", "/Launch", "/URI"}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def check_graph(reader):
    """Inspect reachable and unreferenced indirect objects in the written file."""
    seen = set()
    pages = {(page.indirect_reference.idnum, page.indirect_reference.generation) for page in reader.pages}
    def internal_destination(action):
        action = action.get_object()
        if set(action.keys()) != {"/S", "/D"} or action["/S"] != "/GoTo":
            raise ValueError("Only existing internal GoTo navigation is permitted")
        destination = action["/D"]
        if not isinstance(destination, ArrayObject) or not destination or not isinstance(destination[0], IndirectObject) or (destination[0].idnum, destination[0].generation) not in pages:
            raise ValueError("Navigation target is not a page in this document")
    def visit(value):
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key in seen:
                return
            seen.add(key)
            visit(value.get_object())
        elif isinstance(value, DictionaryObject):
            bad = FORBIDDEN.intersection(value.keys())
            if bad or value.get("/Type") in {"/Action", "/EmbeddedFile", "/Filespec"}:
                raise ValueError(f"Active content or unexpected action: {bad}")
            if "/A" in value:
                internal_destination(value["/A"])
            if "/S" in value:
                internal_destination(value)
            for item in value.values():
                visit(item)
        elif isinstance(value, (ArrayObject, list)):
            for item in value:
                visit(item)
    visit(reader.trailer)
    for generation, entries in reader.xref.items():
        for number in entries:
            if number:
                visit(IndirectObject(number, generation, reader))
    for number in reader.xref_objStm:
        visit(IndirectObject(number, 0, reader))
    return len(seen)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if pypdf.__version__ != "6.10.0":
        raise ValueError("This transformation is bound to pypdf 6.10.0")
    for name, pin in PINS.items():
        if digest(args.raw / name) != pin:
            raise ValueError(f"Raw export differs: {name}")
    args.out.mkdir(parents=False, exist_ok=False)
    receipt = {"status": "RUNNING", "pypdf": pypdf.__version__, "hardware_validated": False, "files": {}}
    receipt_path = args.out / "DERIVATION.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        for name, pin in PINS.items():
            source = args.raw / name
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted or len(reader.pages) != 1 or any(page.get("/Annots") for page in reader.pages):
                raise ValueError("Unexpected encrypted, multipage or annotated input")
            names = reader.trailer["/Root"]["/Names"]
            if set(names.keys()) != {"/JavaScript"}:
                raise ValueError("Unexpected name tree")
            js_tree = names["/JavaScript"]
            entries = js_tree["/Names"]
            if set(js_tree.keys()) != {"/Names"} or len(entries) != 2 or entries[0] != "JSInit":
                raise ValueError("Unexpected JavaScript tree")
            action = entries[1].get_object()
            if set(action.keys()) != {"/Type", "/S", "/JS"} or action["/Type"] != "/Action" or action["/S"] != "/JavaScript" or hashlib.sha256(str(action["/JS"]).encode()).hexdigest() != JS_PIN:
                raise ValueError("Unexpected script")
            # Modify only the in-memory catalog before cloning: the removed objects are not copied.
            del reader.trailer["/Root"]["/Names"]
            writer = PdfWriter()
            writer.clone_document_from_reader(reader)
            target = args.out / name
            with target.open("xb") as stream:
                writer.write(stream)
            written = PdfReader(target, strict=True)
            objects = check_graph(written)
            renders = []
            for label, path in (("raw", source), ("derived", target)):
                prefix = args.out / (path.stem + "-" + label)
                subprocess.run(["pdftoppm", "-r", "150", "-singlefile", "-png", str(path), str(prefix)], check=True, timeout=60)
                renders.append(prefix.with_suffix(".png"))
            if renders[0].read_bytes() != renders[1].read_bytes():
                raise ValueError("Rendered pages differ")
            if digest(source) != pin:
                raise ValueError("Source changed during derivation")
            receipt["files"][name] = {"raw_sha256": pin, "derived_sha256": digest(target), "objects_checked": objects, "render_sha256": digest(renders[0]), "render_byte_equal": True}
        receipt["status"] = "PASS_EXACT_DERIVATION"
    except Exception as exc:
        receipt.update(status="FAILED", error=str(exc))
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

if __name__ == "__main__":
    main()
