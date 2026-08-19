from __future__ import annotations

import os
from pathlib import Path

BASE_URL = "https://elibrary.ferc.gov/eLibrarywebapi/api/"
ELIBRARY_UI_BASE = "https://elibrary.ferc.gov/eLibrary"
USER_AGENT = "ferc-elibrary-mcp/0.1.0 (public eLibrary research)"

SEARCH_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0
CONNECT_TIMEOUT = 10.0

RATE_LIMIT_SECONDS = float(os.environ.get("FERC_RATE_LIMIT_SECONDS", "0.5"))

# eLibrary sits behind a proxy that intermittently returns 502/503/520.
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504, 520, 521, 522, 524})
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0

DEFAULT_DOWNLOAD_DIR = Path(
    os.environ.get("FERC_DOWNLOAD_DIR", str(Path.home() / "Downloads" / "ferc-elibrary"))
)

DEFAULT_SEARCH_LIMIT = 25
MAX_SEARCH_LIMIT = 100
DESCRIPTION_MAX_LEN = 500

DEFAULT_LOOKBACK_DAYS = 60

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
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".rtf": "application/rtf",
}
