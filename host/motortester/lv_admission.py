"""Bounded LV-ADMISSION-1 data inspection, without operational authority.

Only original index bytes and explicitly named local files are inspected.  No
runner, board validator, transport, launcher or executable evidence is imported.
The filesystem model is stable cooperative local storage, not an adversarial
sandbox.  See host/docs/lv-admission-parser.md for the limits of these checks.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PosixPath, WindowsPath
import re
import stat


CONTRACT_COMMIT = "3ac2928fcc714009bc50ec52001ff403fb0bb14e"
CONTRACT_TREE = "306dccc4d0a9f865ea897d66f557caeec9795fd5"
CONTRACT_SHA256 = "63ccc5b0f514b7d0bbcd7c6ef7d166d09ada4dc70c10cd7ad15b46d95df8fd2a"
INDEX_NAME = "admission.json"
MAX_INDEX_BYTES = 16 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
MAX_ROOT_CHARS = 1024
MAX_ROOT_PARTS = 64
ROLES = ("board_passport", "hookup_power_review", "board_config",
         "firmware_image", "firmware_review", "flash_readback",
         "usb_assignment", "signal_review", "measurements", "host_package")
_ROOT_KEYS = frozenset(("contract", "kind", "bundle_id", "board_instance_id",
                        "issued_at", "valid_until", "plan", "identities", "evidence"))
_PLAN_KEYS = frozenset(("method", "warmup_attempts", "main_attempts", "profile",
                        "mode", "source", "pretrigger_samples", "edge",
                        "level_code", "auto_timeout_ms"))
_IDENTITY_KEYS = frozenset(("firmware_build", "firmware_source_sha", "host_source_sha",
                            "host_package_sha256", "usb_vid", "usb_pid",
                            "usb_serial", "port", "computer_id"))
_FILE_KEYS = frozenset(("file", "bytes", "sha256"))
_RESERVED = frozenset(("CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"))
_MARKERS = frozenset(("TBD", "TODO", "UNKNOWN", "PLACEHOLDER", "TEST",
                      "SYNTHETIC", "FIXTURE"))


class AdmissionDataError(ValueError):
    """A bounded diagnostic code, without file contents or backend error text."""


@dataclass(frozen=True, slots=True)
class ExpectedPlan:
    method: str
    warmup_attempts: int
    main_attempts: int
    profile: int
    mode: int
    source: int
    pretrigger_samples: int
    edge: int
    level_code: int
    auto_timeout_ms: int


@dataclass(frozen=True, slots=True)
class ExpectedIdentities:
    firmware_build: int
    firmware_source_sha: str
    host_source_sha: str
    host_package_sha256: str
    usb_vid: int
    usb_pid: int
    usb_serial: str
    port: str
    computer_id: str


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    file: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceRole:
    role: str
    member: EvidenceFile


@dataclass(frozen=True, slots=True)
class ParsedIndex:
    """Schema-consistent claims only; files, time eligibility and truth unchecked."""
    original_bytes: bytes
    sha256: str
    contract: str
    kind: str
    bundle_id: str
    board_instance_id: str
    issued_at: str
    valid_until: str
    plan: ExpectedPlan
    identities: ExpectedIdentities
    roles: tuple[EvidenceRole, ...]
    files: tuple[EvidenceFile, ...]
    unique_bytes: int


@dataclass(frozen=True, slots=True)
class InventoryCheck:
    """Index plus completed byte-size/hash/stability checks, never a grant."""
    index: ParsedIndex
    files: tuple[EvidenceFile, ...]


def _fail(code):
    raise AdmissionDataError(code)


def _shape(text):
    """Bound structure/tokens BEFORE the generic JSON decoder allocates objects.

    The exact schema has depth 3, 14 objects and 68 key/value pairs.  Arrays,
    booleans, null and floating-point forms have no place in it.  The longest
    value is 120 Unicode characters, each at most two escaped UTF-16 units.
    This lexical pass is deliberately not a second complete JSON grammar.
    """
    depth = objects = pairs = 0
    position = 0
    while position < len(text):
        char = text[position]
        if char in " \t\r\n,":
            position += 1
        elif char == '"':
            start = position
            position += 1
            while position < len(text):
                if position - start > 1441:
                    _fail("index-structure")
                char = text[position]
                position += 1
                if char == '"':
                    break
                if char == "\\":
                    position += 1
            else:
                _fail("index-json")
        elif char == "{":
            depth += 1
            objects += 1
            if depth > 3 or objects > 14:
                _fail("index-structure")
            position += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                _fail("index-json")
            position += 1
        elif char == ":":
            pairs += 1
            if pairs > 68:
                _fail("index-structure")
            position += 1
        elif char == "-" or "0" <= char <= "9":
            start = position
            if char == "-":
                position += 1
            digits = position
            while position < len(text) and "0" <= text[position] <= "9":
                position += 1
            if position == digits or position - digits > 10:
                _fail("index-number")
            if position == start:
                _fail("index-json")
        else:
            _fail("index-json")
    if depth != 0:
        _fail("index-json")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail("index-duplicate-key")
        result[key] = value
    return result


def _object(value, keys):
    if type(value) is not dict or value.keys() != keys:
        _fail("index-keys")
    return value


def _string(value, maximum, *, ascii_only=False):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or not value.isprintable() or value != value.strip()
            or (ascii_only and not value.isascii())):
        _fail("index-string")
    # Visible lexical placeholders only.  No claim to classify opaque evidence
    # or detect an unlabelled fabricated story; that requires external review.
    words = re.findall(r"[A-Z]+", value.upper())
    if (any(word in _MARKERS for word in words)
            or re.search(r"NEVER[-_ ]?FLASH", value.upper())):
        _fail("index-placeholder")
    return value


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _fail("index-integer")
    return value


def _hex(value, length):
    if type(value) is not str or re.fullmatch("[0-9a-f]{%d}" % length, value) is None:
        _fail("index-hash")
    return value


def _timestamp(value):
    if type(value) is not str or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value) is None:
        _fail("index-time")
    try:
        datetime.fromisoformat(value[:-1])
    except ValueError:
        raise AdmissionDataError("index-time") from None
    return value


def _member_name(value):
    _string(value, 100, ascii_only=True)
    if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is None
            or value.endswith(".") or ".." in value):
        _fail("index-filename")
    stem = value.split(".", 1)[0].upper()
    if (stem in _RESERVED or re.fullmatch(r"(?:COM|LPT)[0-9]", stem)
            or value.lower() == INDEX_NAME):
        _fail("index-filename")
    return value


def parse_index(raw: bytes) -> ParsedIndex:
    """Parse immutable original bytes, with no filesystem or current-time reads.

    The return value contains expected documentary values, not observed device
    identity, accepted evidence semantics, signature verification or authority.
    """
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_INDEX_BYTES:
        _fail("index-size-or-type")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("index-utf8")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise AdmissionDataError("index-utf8") from None
    _shape(text)
    try:
        root = json.loads(text, object_pairs_hook=_pairs)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, AdmissionDataError):
            raise
        raise AdmissionDataError("index-json") from None
    _object(root, _ROOT_KEYS)
    if root["contract"] != "LV-ADMISSION-1" or root["kind"] != "physical-lv-dc":
        _fail("index-version-or-kind")
    bundle = _string(root["bundle_id"], 80, ascii_only=True)
    board = _string(root["board_instance_id"], 80)
    issued = _timestamp(root["issued_at"])
    until = _timestamp(root["valid_until"])
    if issued >= until:
        _fail("index-time-order")

    plan = _object(root["plan"], _PLAN_KEYS)
    if plan["method"] != "LV-001":
        _fail("index-plan")
    for name, fixed in (("warmup_attempts", 1), ("main_attempts", 20),
                        ("profile", 1), ("mode", 0), ("source", 0),
                        ("pretrigger_samples", 0)):
        _integer(plan[name], fixed, fixed)
    _integer(plan["edge"], 0, 1)
    _integer(plan["level_code"], 1, 4094)
    _integer(plan["auto_timeout_ms"], 100, 60000)

    identities = _object(root["identities"], _IDENTITY_KEYS)
    _integer(identities["firmware_build"], 0, 0xffffffff)
    _hex(identities["firmware_source_sha"], 40)
    _hex(identities["host_source_sha"], 40)
    _hex(identities["host_package_sha256"], 64)
    _integer(identities["usb_vid"], 1, 65534)
    _integer(identities["usb_pid"], 0, 65535)
    serial = _string(identities["usb_serial"], 31, ascii_only=True)
    if re.fullmatch(r"[A-Za-z0-9]+", serial) is None:
        _fail("index-usb-serial")
    _string(identities["port"], 120)
    _string(identities["computer_id"], 80)

    evidence = _object(root["evidence"], frozenset(ROLES))
    unique = {}
    folded = {}
    roles = []
    total = 0
    for role in ROLES:
        declaration = _object(evidence[role], _FILE_KEYS)
        name = _member_name(declaration["file"])
        size = _integer(declaration["bytes"], 1, MAX_FILE_BYTES)
        digest = _hex(declaration["sha256"], 64)
        member = EvidenceFile(name, size, digest)
        if name.lower() in folded and folded[name.lower()] != name:
            _fail("index-case-alias")
        folded[name.lower()] = name
        if name in unique:
            if member != unique[name]:
                _fail("index-alias-conflict")
            member = unique[name]
        else:
            total += size
            if total > MAX_TOTAL_BYTES:
                _fail("index-total-size")
            unique[name] = member
        roles.append(EvidenceRole(role, member))
    return ParsedIndex(raw, hashlib.sha256(raw).hexdigest(), root["contract"],
                       root["kind"], bundle, board, issued, until,
                       ExpectedPlan(**plan), ExpectedIdentities(**identities),
                       tuple(roles), tuple(unique.values()), total)


def _fingerprint(info):
    # atime may change due to our reads.  This tuple is compared only against
    # later results from the SAME API: Windows lstat and fstat differ.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink,
            getattr(info, "st_file_attributes", 0),
            getattr(info, "st_reparse_tag", 0))


def _regular(info, *, directory=False):
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or getattr(info, "st_reparse_tag", 0)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        _fail("filesystem-type")


def _windows_local_drive(anchor):
    # Metadata only.  UNC/device roots are already rejected lexically.  A mapped
    # remote drive must not quietly turn an explicit local root into network I/O.
    import ctypes
    get_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    get_type.argtypes = (ctypes.c_wchar_p,)
    get_type.restype = ctypes.c_uint
    if get_type(anchor) not in (2, 3, 5, 6):
        _fail("filesystem-local-root")


def _root_paths(directory):
    if type(directory) is str:
        name = directory
    elif type(directory) in (Path, PosixPath, WindowsPath):
        name = str(directory)
    else:
        _fail("filesystem-root")
    if not 1 <= len(name) <= MAX_ROOT_CHARS or not name.isprintable():
        _fail("filesystem-root")
    if name.startswith(("//", "\\\\")):
        _fail("filesystem-local-root")
    path = Path(name)
    if not path.is_absolute() or len(path.parts) > MAX_ROOT_PARTS:
        _fail("filesystem-root")
    # Do not resolve away evidence of redirects, '..', or ambiguous spellings.
    if os.name == "nt":
        if re.fullmatch(r"[A-Za-z]:\\", path.anchor) is None:
            _fail("filesystem-local-root")
        remainder = name[len(path.anchor):]
        if "/" in name or ":" in remainder:
            _fail("filesystem-root")
        pieces = remainder.split("\\") if remainder else []
        _windows_local_drive(path.anchor)
    else:
        if "\\" in name or ":" in name:
            _fail("filesystem-local-root")
        pieces = name[1:].split("/") if name != "/" else []
    if any(part in ("", ".", "..") or part.endswith((".", " ")) for part in pieces):
        _fail("filesystem-root")
    paths = tuple(reversed(path.parents)) + (path,)
    return path, tuple((item, _directory_fingerprint(item)) for item in paths)


def _directory_fingerprint(path):
    info = path.lstat()
    _regular(info, directory=True)
    # Unrelated child creation may change directory size/timestamps without
    # redirecting these explicit paths.  Compare directory identity/type only.
    return (info.st_dev, info.st_ino, info.st_mode,
            getattr(info, "st_file_attributes", 0),
            getattr(info, "st_reparse_tag", 0))


def _stable_directories(paths):
    for path, before in paths:
        if _directory_fingerprint(path) != before:
            _fail("filesystem-changed")


@dataclass(frozen=True, slots=True)
class _Opened:
    path: Path
    fd: int
    path_before: tuple
    handle_before: tuple


def _stable_file(opened):
    path_info = opened.path.lstat()
    handle_info = os.fstat(opened.fd)
    _regular(path_info)
    _regular(handle_info)
    if (_fingerprint(path_info) != opened.path_before
            or _fingerprint(handle_info) != opened.handle_before):
        _fail("filesystem-changed")


def _open_file(path, maximum, opened_files):
    before = path.lstat()
    _regular(before)
    if not 1 <= before.st_size <= maximum:
        _fail("filesystem-size")
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    fd = os.open(path, flags)
    try:
        handle = os.fstat(fd)
        _regular(handle)
        if not handle.st_ino:
            _fail("filesystem-file-identity")
        if not 1 <= handle.st_size <= maximum:
            _fail("filesystem-size")
        opened = _Opened(path, fd, _fingerprint(before), _fingerprint(handle))
        _stable_file(opened)
        opened_files.append(opened)
    except BaseException:
        os.close(fd)
        raise
    return opened


def _read_file(opened, maximum, *, retain=False):
    hasher = hashlib.sha256()
    parts = []
    count = 0
    while count < maximum:
        chunk = os.read(opened.fd, min(READ_CHUNK_BYTES, maximum - count))
        if not chunk:
            break
        count += len(chunk)
        hasher.update(chunk)
        if retain:
            parts.append(chunk)
    if os.read(opened.fd, 1):
        _fail("filesystem-size")
    _stable_file(opened)
    if count != opened.path_before[3] or count != opened.handle_before[3]:
        _fail("filesystem-size")
    return count, hasher.hexdigest(), b"".join(parts) if retain else None


def verify_bundle(directory: str | Path) -> InventoryCheck:
    """Read admission.json and its declared inventory, without changing files.

    Only this successful return means that every unique member's bytes matched
    the index during this check.  Nothing establishes document meaning, current
    eligibility, actual equipment identity, human authority or one-run consent.
    A later consumer must not use this detached snapshot as an operative token.
    """
    opened_files = []
    try:
        root, directories = _root_paths(directory)
        opened = _open_file(root / INDEX_NAME, MAX_INDEX_BYTES, opened_files)
        _, _, raw = _read_file(opened, MAX_INDEX_BYTES, retain=True)
        parsed = parse_index(raw)
        identities = {(opened.handle_before[0], opened.handle_before[1])}
        for member in parsed.files:
            _stable_directories(directories)
            opened = _open_file(root / member.file, member.bytes, opened_files)
            identity = (opened.handle_before[0], opened.handle_before[1])
            if identity in identities:
                _fail("filesystem-file-alias")
            identities.add(identity)
            size, digest, _ = _read_file(opened, member.bytes)
            if size != member.bytes or digest != member.sha256:
                _fail("filesystem-integrity")
        for opened in opened_files:
            _stable_file(opened)
        _stable_directories(directories)
        return InventoryCheck(parsed, parsed.files)
    except (OSError, ValueError) as exc:
        if isinstance(exc, AdmissionDataError):
            raise
        raise AdmissionDataError("filesystem-read") from None
    finally:
        close_failed = False
        for opened in reversed(opened_files):
            try:
                os.close(opened.fd)
            except OSError:
                close_failed = True
        if close_failed:
            _fail("filesystem-close")
