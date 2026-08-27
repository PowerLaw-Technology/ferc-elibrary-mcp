from __future__ import annotations

import os
import re
from pathlib import Path

BASE_URL = "https://elibrary.ferc.gov/eLibrarywebapi/api/"
ELIBRARY_UI_BASE = "https://elibrary.ferc.gov/eLibrary"
USER_AGENT = "ferc-elibrary-mcp/0.1.2 (public eLibrary research)"

SEARCH_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0
CONNECT_TIMEOUT = 10.0

RATE_LIMIT_SECONDS = float(os.environ.get("FERC_RATE_LIMIT_SECONDS", "0.5"))

# eLibrary sits behind a proxy that intermittently returns 502/503/520.
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504, 520, 521, 522, 524})
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0

# Claude Desktop's MCPB host has been observed passing user_config defaults
# through as literals (`${HOME}/Downloads/ferc-elibrary`) instead of expanding
# them. Resolve placeholders here so files never land under the extension dir.
_DOWNLOAD_DIR_PLACEHOLDERS = {
    "HOME": lambda: str(Path.home()),
    "USERPROFILE": lambda: str(Path.home()),
    "DESKTOP": lambda: str(Path.home() / "Desktop"),
    "DOCUMENTS": lambda: str(Path.home() / "Documents"),
    "DOWNLOADS": lambda: str(Path.home() / "Downloads"),
}
_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def resolve_download_dir(raw: str | None = None) -> Path:
    """Return an absolute download directory, expanding MCPB/shell placeholders.

    Unset or blank ``FERC_DOWNLOAD_DIR`` uses ``~/Downloads/ferc-elibrary``.
    """
    if raw is None:
        raw = os.environ.get("FERC_DOWNLOAD_DIR", "")
    value = (raw or "").strip()
    if not value:
        return Path.home() / "Downloads" / "ferc-elibrary"

    def _subst(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in {"pathSeparator", "/"}:
            return os.sep
        factory = _DOWNLOAD_DIR_PLACEHOLDERS.get(name.upper())
        return factory() if factory else match.group(0)

    expanded = _PLACEHOLDER_RE.sub(_subst, value)
    expanded = os.path.expandvars(expanded)
    expanded = os.path.expanduser(expanded)
    path = Path(expanded)
    if not path.is_absolute():
        path = Path.home() / path
    return path


DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "ferc-elibrary"

DEFAULT_SEARCH_LIMIT = 25
MAX_SEARCH_LIMIT = 100
DESCRIPTION_MAX_LEN = 500

DEFAULT_LOOKBACK_DAYS = 60

# GetSingleDocketSheet ignores numHits/pageNumber slicing and returns the whole
# association set, so the sheet is fetched once and paginated client-side.
DOCKET_SHEET_FETCH_LIMIT = 5000

# Paging bound when resolving a docket sheet's issuance window through search.
MAX_CROSS_REFERENCE_PAGES = 20

# Claude Desktop sometimes spawns duplicate stdio servers and abandons one
# without closing its stdin, so the abandoned process never sees EOF and idles
# forever. Set a positive number of seconds to have an instance that has
# received no traffic shut itself down. Disabled by default because a healthy
# but merely unused server would also exit, and recovery then depends on the
# client respawning it.
IDLE_SHUTDOWN_SECONDS = float(os.environ.get("FERC_MCP_IDLE_TIMEOUT_SECONDS", "0"))

# eLibrary silently returns zero hits for an unrecognized dateType, so only
# these verified values may ever reach the wire.
DATE_FIELD_TYPES = {"filed": "filed_date", "issued": "issued_date"}

# A "PUBLIC"-prefixed or "REDACTED" file name conventionally implies a sealed
# counterpart on the same accession. Utility names beginning with "Public
# Service"/"Public Utility" are the obvious false positives.
NONPUBLIC_PREFIX_RE = r"^\s*public[\s_\-.]+"
NONPUBLIC_PREFIX_EXCEPTIONS = (
    "service",
    "utility",
    "utilities",
    "interest",
    "version of",
)
NONPUBLIC_KEYWORDS = ("redacted", "public version", "public copy", "non-ceii")

COLLECT_MAX_DOCKETS = 10
COLLECT_MAX_FILINGS_PER_DOCKET = 50
COLLECT_MAX_DOWNLOADS = 10
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024

# Text returned through MCP tool responses (agents cannot open local paths).
DEFAULT_EXTRACT_CHARS = int(os.environ.get("FERC_DEFAULT_EXTRACT_CHARS", "20000"))
MAX_EXTRACT_CHARS = int(os.environ.get("FERC_MAX_EXTRACT_CHARS", "100000"))
MAX_EXTRACT_FILE_BYTES = int(
    os.environ.get("FERC_MAX_EXTRACT_FILE_BYTES", str(15 * 1024 * 1024))
)

# Cross-accession Zip & Download: eLibrary accepts many file IDs in one
# DownloadP8File call (UI allows up to ~10 GB). Cap below that so a runaway
# collect cannot fill the disk; raise via max_bytes if needed.
MAX_BUNDLE_BYTES = int(
    os.environ.get("FERC_MAX_BUNDLE_BYTES", str(500 * 1024 * 1024))
)
MAX_BUNDLE_FILES = int(os.environ.get("FERC_MAX_BUNDLE_FILES", "100"))
BUNDLE_DOWNLOAD_TIMEOUT = float(os.environ.get("FERC_BUNDLE_TIMEOUT_SECONDS", "300"))

PUBLIC_AVAIL_CODES = {"p"}
RESTRICTED_AVAIL = {
    "c": "CEII",
    "s": "Protected",
    "n": "Privileged",
}

INDUSTRY_ALIASES = {
    "electric": "Electric",
    "natural gas": "Gas",
    "gas": "Gas",
    "oil": "Oil",
    "rulemaking": "Rulemaking",
    "hydro": "Hydro",
    "general": "General",
}

CATEGORY_ALIASES = {
    "issuance": "Issuance",
    "submittal": "Submittal",
}

# eLibrary serves every download as application/octet-stream, so the real type
# has to be inferred from magic bytes or the file extension.
MAGIC_CONTENT_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\xd0\xcf\x11\xe0", "application/vnd.ms-office"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)

EXTENSION_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".rtf": "application/rtf",
}

# OOXML packages are ZIP containers (PK..). Prefer the extension over zip magic.
OOXML_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"})
