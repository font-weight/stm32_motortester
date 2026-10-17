"""Fixed Windows 10 1809+ x64 packaging policy, applied after Analysis.

This pure TOC transform does not establish native-file provenance or TLS support.
See ../docs/windows-smoke.md for the primary basis and remaining Windows gates.
"""

# Exact approved destinations, relative to the onedir _internal directory.
# Absence is allowed; this is not a list of required runtime dependencies.
EXCLUDED_DESTINATIONS = (
    'api-ms-win-core-console-l1-1-0.dll',
    'api-ms-win-core-datetime-l1-1-0.dll',
    'api-ms-win-core-debug-l1-1-0.dll',
    'api-ms-win-core-errorhandling-l1-1-0.dll',
    'api-ms-win-core-fibers-l1-1-0.dll',
    'api-ms-win-core-fibers-l1-1-1.dll',
    'api-ms-win-core-file-l1-1-0.dll',
    'api-ms-win-core-file-l1-2-0.dll',
    'api-ms-win-core-file-l2-1-0.dll',
    'api-ms-win-core-handle-l1-1-0.dll',
    'api-ms-win-core-heap-l1-1-0.dll',
    'api-ms-win-core-interlocked-l1-1-0.dll',
    'api-ms-win-core-kernel32-legacy-l1-1-1.dll',
    'api-ms-win-core-libraryloader-l1-1-0.dll',
    'api-ms-win-core-localization-l1-2-0.dll',
    'api-ms-win-core-memory-l1-1-0.dll',
    'api-ms-win-core-namedpipe-l1-1-0.dll',
    'api-ms-win-core-processenvironment-l1-1-0.dll',
    'api-ms-win-core-processthreads-l1-1-0.dll',
    'api-ms-win-core-processthreads-l1-1-1.dll',
    'api-ms-win-core-profile-l1-1-0.dll',
    'api-ms-win-core-rtlsupport-l1-1-0.dll',
    'api-ms-win-core-string-l1-1-0.dll',
    'api-ms-win-core-synch-l1-1-0.dll',
    'api-ms-win-core-synch-l1-2-0.dll',
    'api-ms-win-core-sysinfo-l1-1-0.dll',
    'api-ms-win-core-sysinfo-l1-2-0.dll',
    'api-ms-win-core-timezone-l1-1-0.dll',
    'api-ms-win-core-util-l1-1-0.dll',
    'api-ms-win-crt-conio-l1-1-0.dll',
    'api-ms-win-crt-convert-l1-1-0.dll',
    'api-ms-win-crt-environment-l1-1-0.dll',
    'api-ms-win-crt-filesystem-l1-1-0.dll',
    'api-ms-win-crt-heap-l1-1-0.dll',
    'api-ms-win-crt-locale-l1-1-0.dll',
    'api-ms-win-crt-math-l1-1-0.dll',
    'api-ms-win-crt-private-l1-1-0.dll',
    'api-ms-win-crt-process-l1-1-0.dll',
    'api-ms-win-crt-runtime-l1-1-0.dll',
    'api-ms-win-crt-stdio-l1-1-0.dll',
    'api-ms-win-crt-string-l1-1-0.dll',
    'api-ms-win-crt-time-l1-1-0.dll',
    'api-ms-win-crt-utility-l1-1-0.dll',
    'libcrypto-3-x64.dll',
    'libssl-3-x64.dll',
    'PySide6/plugins/tls/qopensslbackend.dll',
    'ucrtbase.dll',
)

REQUIRED_DESTINATIONS = (
    "PySide6/Qt6Network.dll",
    "PySide6/QtNetwork.pyd",
    "PySide6/plugins/tls/qschannelbackend.dll",
    "_ssl.pyd",
    "_hashlib.pyd",
    "libcrypto-3.dll",
    "libssl-3.dll",
)

_EXCLUDED = {name.casefold(): name for name in EXCLUDED_DESTINATIONS}
_EXCLUDED_BASENAMES = {name.rsplit("/", 1)[-1] for name in _EXCLUDED}
_REQUIRED = {name.casefold() for name in REQUIRED_DESTINATIONS}
_RESERVED = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"} | {
    prefix + digit for prefix in ("com", "lpt") for digit in "0123456789¹²³"
}


def _destination_key(name):
    """Compare Windows destinations without normalizing away unsafe spellings."""
    if not name or any(ord(char) < 32 or ord(char) == 127 or char in '<>:"|?*'
                       for char in name):
        raise ValueError(f"Unsafe Windows TOC destination: {name!r}")
    parts = name.replace("\\", "/").split("/")
    if any(not part or part in (".", "..") or part.endswith((".", " "))
           or part.split(".", 1)[0].rstrip(" ").casefold() in _RESERVED
           for part in parts):
        raise ValueError(f"Unsafe Windows TOC destination: {name!r}")
    return "/".join(parts).casefold()


def _needs_review(basename):
    # These are rejection guards, never rules authorizing additional deletion.
    return (basename.startswith(("api-ms-win-core-", "api-ms-win-crt-"))
            or (basename.startswith(("ucrtbase", "qopensslbackend")) and basename.endswith(".dll"))
            or (basename.startswith(("libcrypto-", "libssl-"))
                and "-x64" in basename and basename.endswith(".dll")))


def select_windows_runtime(binaries):
    """Return (unchanged retained rows, canonical names actually removed).

    Source paths are opaque: no filesystem access, source hashing, imports or
    native execution. Rebuilt dependency files can use different source paths.
    Only the two binary TOC types produced by Analysis are accepted.
    """
    retained, removed, seen = [], [], set()
    for row in binaries:
        if (not isinstance(row, (tuple, list)) or len(row) != 3
                or not all(isinstance(value, str) and value for value in row)
                or row[2] not in ("BINARY", "EXTENSION") or "\x00" in row[1]):
            raise ValueError(f"Malformed binary TOC row: {row!r}")
        destination, _source, kind = row
        key = _destination_key(destination)
        if key in seen:
            raise ValueError(f"Colliding Windows TOC destination: {destination!r}")
        seen.add(key)
        if key in _REQUIRED and key.endswith(".dll") and kind != "BINARY":
            raise ValueError(f"Required DLL has unexpected type: {destination!r}")
        basename = key.rsplit("/", 1)[-1]
        if key in _EXCLUDED:
            if kind != "BINARY":
                raise ValueError(f"Excluded destination has unexpected type: {destination!r}")
            removed.append(_EXCLUDED[key])
        elif basename in _EXCLUDED_BASENAMES or _needs_review(basename):
            raise ValueError(f"Unreviewed runtime destination: {destination!r}")
        else:
            retained.append(row)
    missing = _REQUIRED - seen
    if missing:
        raise ValueError("Missing required runtime destinations: " + ", ".join(sorted(missing)))
    return retained, tuple(removed)
