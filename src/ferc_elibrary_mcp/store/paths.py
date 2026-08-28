from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)


def safe_name(name: str) -> str:
    cleaned = _UNSAFE.sub("_", name).strip(" ._")
    return cleaned[:180]


def normalize_docket(docket: str) -> str:
    return safe_name(docket.strip())


def accession_dir(root: Path, docket: str, accession: str) -> Path:
    return root / "dockets" / normalize_docket(docket) / safe_name(accession)


def manifest_path(root: Path, docket: str, accession: str) -> Path:
    return accession_dir(root, docket, accession) / "manifest.json"


def docket_index_path(root: Path, docket: str) -> Path:
    return root / "dockets" / normalize_docket(docket) / "docket-index.json"


def extracted_text_path(root: Path, docket: str, accession: str, filename: str) -> Path:
    return accession_dir(root, docket, accession) / f"{filename}.extracted.txt"


def pages_json_path(root: Path, docket: str, accession: str, filename: str) -> Path:
    return accession_dir(root, docket, accession) / f"{filename}.pages.json"


def stored_file_path(root: Path, docket: str, accession: str, filename: str) -> Path:
    return accession_dir(root, docket, accession) / safe_name(filename)


def legacy_v1_file_path(root: Path, accession: str, filename: str) -> Path:
    return root / safe_name(accession) / safe_name(filename)
