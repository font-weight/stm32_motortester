"""Build a bounded, source-driven offline documentation draft; never deploy."""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit

EXPORT_ROOT = "hardware/analog-exports/"
EXPORT_FILES = {EXPORT_ROOT + "exports/" + stem + ext
                for stem in ("option-a-dc", "option-b-ac", "structural", "functional")
                for ext in (".pdf", ".svg")}
EXPORT_META = {EXPORT_ROOT + "MANIFEST.json", EXPORT_ROOT + "evidence/EXPORT_OBSERVATION.json",
               EXPORT_ROOT + "evidence/PDF_DERIVATION.json"}


def reject_original_link(value):
    decoded = value
    for _ in range(4):
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    if "original-kicad-pdf" in decoded.replace("\\", "/").lower():
        raise ValueError("Original JavaScript PDF links are excluded from this site")


def verify_export_inputs(manifest, data, page_sources, downloads):
    binding = manifest["asset_manifest"]
    manifest_path = EXPORT_ROOT + "MANIFEST.json"
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"} or binding["path"] != manifest_path:
        raise ValueError("Invalid export-manifest binding")
    if not isinstance(binding["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]):
        raise ValueError("Invalid export-manifest digest")
    selected = {name for name in page_sources + downloads if name.startswith(EXPORT_ROOT)}
    expected = EXPORT_FILES | EXPORT_META | {EXPORT_ROOT + "README_RU.md"}
    if selected != expected or not EXPORT_FILES.issubset(downloads) or EXPORT_ROOT + "README_RU.md" not in page_sources:
        raise ValueError("Unexpected or missing analog export selection")
    if sha(data[manifest_path]) != binding["sha256"]:
        raise ValueError("Export manifest differs from its reviewed pin")
    package = json.loads(data[manifest_path])
    if package.get("scope") != "draft analog generated delivery; hardware_validated=false" or not isinstance(package.get("files"), dict):
        raise ValueError("Unexpected export manifest format")
    for name in selected - {manifest_path}:
        record = package["files"].get(name[len(EXPORT_ROOT):])
        if record != {"bytes": len(data[name]), "sha256": sha(data[name])}:
            raise ValueError("Selected export differs from reviewed manifest: " + name)
    originals = {record["sha256"] for name, record in package["files"].items()
                 if name.startswith("evidence/original-kicad-pdf/")}
    if len(originals) != 2 or any(sha(value) in originals for value in data.values()):
        raise ValueError("Original PDF bytes may not be copied into the site")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return quote(value, safe="/-._~")


def relative(target, page):
    return encoded(posixpath.relpath(target, posixpath.dirname(page) or "."))


def source_file(root, name, directory=False):
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or any(p in (".", "..") or p.startswith(".") for p in parts):
        raise ValueError("Invalid source path: " + name)
    path = root.joinpath(*parts)
    if path.resolve() != path or not path.exists():
        raise ValueError("Missing or aliased source: " + name)
    if not directory and not path.is_file():
        raise ValueError("Source is not a file: " + name)
    return path


def plain(node):
    if isinstance(node, list):
        return "".join(plain(x) for x in node)
    if not isinstance(node, dict):
        return ""
    kind, value = node.get("t"), node.get("c")
    if kind == "Str":
        return value
    if kind in ("Space", "SoftBreak", "LineBreak"):
        return " "
    if kind in ("Code", "Math"):
        return value[-1]
    return plain(value)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links = set(), []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            if attrs["id"] in self.ids:
                raise ValueError("Duplicate HTML id: " + attrs["id"])
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                reject_original_link(attrs[key])
                self.links.append(attrs[key])
        if tag in ("script", "iframe", "object", "embed"):
            raise ValueError("Active/embedded content is outside this delivery")


def validate_links(out, pages):
    parsed = {}
    for page in pages:
        parser = Links()
        parser.feed((out / page).read_text(encoding="utf-8"))
        parsed[page] = parser
    checked = 0
    for page, parser in parsed.items():
        for link in parser.links:
            url = urlsplit(link)
            if url.scheme in ("https", "http", "mailto"):
                continue
            if url.scheme or url.netloc or url.path.startswith("/"):
                raise ValueError("Nonportable local link: " + link)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page), unquote(url.path))) if url.path else page
            if target.startswith("../") or not (out / target).is_file():
                raise ValueError(f"Broken output link: {page}: {link}")
            if url.fragment and target in parsed and unquote(url.fragment) not in parsed[target].ids:
                raise ValueError(f"Broken output anchor: {page}: {link}")
            checked += 1
    return checked


def build(args):
    root, out = Path(args.source_root).absolute(), Path(args.out).absolute()
    if root.resolve() != root or not root.is_dir() or out.resolve() != out:
        raise ValueError("Use normalized absolute non-symlink paths")
    if out.exists() or not out.parent.is_dir() or out.is_relative_to(root) or root.is_relative_to(out):
        raise ValueError("Output must be new, outside source, with an existing parent")
    if Path(__file__).resolve() != source_file(root, "site/build.py"):
        raise ValueError("Execute site/build.py from the declared source root")
    renderer = shutil.which("pandoc")
    if not renderer:
        raise ValueError("Pandoc is required; nothing is installed automatically")
    renderer = Path(renderer).resolve()
    version = subprocess.run([str(renderer), "--version"], capture_output=True, check=True, timeout=30).stdout.decode()
    if not version.startswith("pandoc 3."):
        raise ValueError("This builder requires Pandoc 3; tested version is documented")
    manifest = json.loads(source_file(root, "site/pages.json").read_bytes())
    if set(manifest) != {"schema", "external_reference", "pages", "downloads", "asset_manifest"} or manifest["schema"] != 2:
        raise ValueError("Unsupported source manifest")
    ref = manifest["external_reference"]
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("External references must use an immutable commit")
    page_sources = ["site/README.md", *manifest["pages"]]
    downloads = manifest["downloads"]
    if len(set(page_sources + downloads)) != len(page_sources + downloads):
        raise ValueError("Duplicate source manifest entry")
    if any(not name.endswith(".md") or name.startswith("host/") for name in page_sources):
        raise ValueError("Only explicit A-owned Markdown pages are rendered")
    names = [*page_sources, *downloads, "site/pages.json", "site/style.css", "site/build.py"]
    data = {name: source_file(root, name).read_bytes() for name in names}
    verify_export_inputs(manifest, data, page_sources, downloads)
    inputs = {name: sha(value) for name, value in sorted(data.items())}
    identity = sha(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode())
    outputs = {name: "index.html" if name == "site/README.md" else "pages/" + name[:-3] + ".html" for name in page_sources}
    raw = {name: "sources/" + name for name in page_sources + downloads}
    trees, titles, commands = {}, {}, []

    def pandoc(source, from_type, to_type):
        command = [str(renderer), "--from=" + from_type, "--to=" + to_type, "--wrap=none"]
        result = subprocess.run(command, input=source, capture_output=True, timeout=60)
        commands.append({"argv": ["pandoc", *command[1:]], "returncode": result.returncode,
                         "stderr": result.stderr.decode("utf-8", errors="replace")})
        if result.returncode:
            raise ValueError("Pandoc failed: " + commands[-1]["stderr"])
        return result.stdout

    # Parse all sources before creating output. Raw HTML is disabled; no filters run.
    for name in page_sources:
        tree = json.loads(pandoc(data[name], "gfm-raw_html", "json"))
        trees[name] = tree
        headers = [b for b in tree["blocks"] if b["t"] == "Header"]
        titles[name] = plain(headers[0]["c"][2]) if headers else name

    online = set()

    def rewrite(node, source):
        if isinstance(node, list):
            return [rewrite(n, source) for n in node]
        if not isinstance(node, dict):
            return node
        if node.get("t") in ("RawBlock", "RawInline"):
            raise ValueError("Raw content is outside the documentation build")
        if node.get("t") == "Image":
            node = {"t": "Link", "c": node["c"]}
            node["c"][1].insert(0, {"t": "Str", "c": "Изображение (ссылка): "})
        if node.get("t") == "Link":
            attr, label, target = node["c"]
            reject_original_link(target[0])
            url = urlsplit(target[0])
            external = bool(url.scheme or url.netloc)
            if external:
                if url.scheme not in ("https", "http", "mailto") or url.netloc and not url.scheme:
                    raise ValueError("Unsupported link scheme: " + target[0])
            else:
                path = unquote(url.path)
                if path.startswith("/") or "\\" in path:
                    raise ValueError("Nonportable source link: " + path)
                local = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
                actual = source_file(root, local, directory=True)
                fragment = "#" + encoded(unquote(url.fragment)) if url.fragment else ""
                if local in outputs:
                    target[0] = relative(outputs[local], outputs[source]) + fragment
                elif local in raw:
                    target[0] = relative(raw[local], outputs[source]) + fragment
                else:
                    kind = "tree" if actual.is_dir() else "blob"
                    target[0] = f"https://github.com/font-weight/stm32_motortester/{kind}/{ref}/" + encoded(local) + fragment
                    external = True
            if external:
                attr[1].append("online")
                label.extend([{"t": "Space"}, {"t": "Str", "c": "↗ онлайн"}])
                online.add(target[0])
        if node.get("t") == "CodeBlock" and "mermaid" in node["c"][0][1]:
            return {"t": "Div", "c": [["", ["diagram-source"], []], [
                {"t": "Para", "c": [{"t": "Str", "c": "Исходный текст диаграммы; графический экспорт здесь не включён."}]}, node]]}
        return {key: rewrite(value, source) for key, value in node.items()}

    for name in trees:
        trees[name] = rewrite(trees[name], name)
    out.mkdir()
    receipt = {"schema": 2, "status": "RUNNING", "documentation_draft": True,
               "source_content_sha256": identity, "external_reference_commit": ref,
               "inputs": inputs, "python_version": sys.version, "renderer_version": version, "renderer_sha256": sha(renderer.read_bytes()),
               "commands": commands, "physical_verified": False, "pdf_included": True,
               "schematic_exports_included": True, "explanatory_pdf_included": False, "host_help_included": False,
               "asset_manifest": manifest["asset_manifest"],
               "included_downloads": {name: {"path": raw[name], "bytes": len(data[name]), "sha256": sha(data[name])} for name in downloads}}
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        (out / "style.css").write_bytes(data["site/style.css"])
        for name, path in raw.items():
            dest = out / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data[name])
        for name, tree in trees.items():
            page = outputs[name]
            body = pandoc(json.dumps(tree, ensure_ascii=False).encode(), "json", "html5").decode()
            nav = "".join('<li><a href="' + relative(outputs[n], page) + '">' + html.escape(titles[n]) + '</a></li>' for n in page_sources)
            warning = "Черновик документации. Общая приёмка A/B, Windows и физические проверки не подтверждены этим сайтом. Синтетические сборки не предназначены для прошивки."
            text = ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'self\'; img-src \'self\'; base-uri \'none\'; form-action \'none\'">'
                    '<title>' + html.escape(titles[name]) + ' · Мотортестер</title><link rel="stylesheet" href="' + relative("style.css", page) + '"></head><body>'
                    '<a class="skip" href="#document-content">К содержимому</a><header><a href="' + relative("index.html", page) + '">Мотортестер · локальная документация</a>'
                    '<p class="warning">' + warning + '</p></header><div class="layout"><aside><details open><summary>Разделы</summary><nav aria-label="Документы"><ul>' + nav + '</ul></nav></details></aside>'
                    '<main id="document-content"><article>' + body + '</article><footer><p><a href="' + relative(raw[name], page) + '">Исходный Markdown</a></p>'
                    '<p>Набор входных байтов SHA256: <code>' + identity + '</code></p><p>Внешние исходники: <code>' + ref + '</code>. Ссылки «↗ онлайн» требуют сети; в автономный комплект они не входят.</p>'
                    '<p>Включены четыре PDF и четыре SVG низковольтных кандидатов; это не полная схема проверенного прибора. Пояснительный PDF и справка приложения B не включены.</p></footer></main></div></body></html>')
            dest = out / page
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        receipt["local_links_checked"] = validate_links(out, outputs.values())
        after = {name: sha(source_file(root, name).read_bytes()) for name in names}
        if after != inputs:
            raise ValueError("Source changed during build")
        receipt.update(status="PASS", included_pages=outputs, online_links=sorted(online),
                       output_sha256={p.relative_to(out).as_posix(): sha(p.read_bytes()) for p in sorted(out.rglob("*")) if p.is_file() and p.name != "receipt.json"})
    except Exception as error:
        receipt.update(status="FAILED", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "pages": len(outputs), "source_content_sha256": identity}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    try:
        build(arguments)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
