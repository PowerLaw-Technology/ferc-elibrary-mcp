from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class StoredFile(BaseModel):
    filename: str
    file_id: str = ""
    size_bytes: int = 0
    sha256: str = ""
    page_count: int | None = None
    extracted_char_count: int | None = None
    content_type: str = ""
    fetched_at: str = ""
    ocr_used: bool = False
    extractor: str = ""


class AccessionManifest(BaseModel):
    accession_number: str
    docket_numbers: list[str] = Field(default_factory=list)
    filed_date: str = ""
    issued_date: str | None = None
    description: str = ""
    document_types: list[str] = Field(default_factory=list)
    files: list[StoredFile] = Field(default_factory=list)
    fetch_timestamp: str = Field(default_factory=utc_now_iso)


class DocketIndexEntry(BaseModel):
    accession_number: str
    filed_date: str = ""
    description: str = ""


class DocketIndex(BaseModel):
    docket_number: str
    accessions: list[DocketIndexEntry] = Field(default_factory=list)
    latest_filing_date: str = ""
    last_synced_at: str = Field(default_factory=utc_now_iso)


class CacheStatus(BaseModel):
    backend: str
    root: str
    docket_number: str | None = None
    accession_number: str | None = None
    accessions: list[dict[str, Any]] = Field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
