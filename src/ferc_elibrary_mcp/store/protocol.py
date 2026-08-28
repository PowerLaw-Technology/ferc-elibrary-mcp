from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ferc_elibrary_mcp.store.models import AccessionManifest, CacheStatus, DocketIndex


class DocumentStore(Protocol):
    @property
    def backend(self) -> str: ...

    @property
    def root(self) -> Path: ...

    def exists(self, docket: str, accession: str, filename: str) -> bool: ...

    def accession_cached(self, docket: str, accession: str) -> bool: ...

    def get(self, docket: str, accession: str, filename: str) -> Path | None: ...

    def put(
        self,
        docket: str,
        accession: str,
        filename: str,
        body: bytes,
        *,
        file_id: str = "",
        content_type: str = "",
    ) -> Path: ...

    def list_files(self, docket: str, accession: str) -> list[str]: ...

    def manifest(self, docket: str, accession: str) -> AccessionManifest | None: ...

    def save_manifest(self, docket: str, accession: str, manifest: AccessionManifest) -> None: ...

    def docket_index(self, docket: str) -> DocketIndex | None: ...

    def update_docket_index(self, docket: str, index: DocketIndex) -> None: ...

    def cache_status(
        self,
        *,
        docket: str | None = None,
        accession: str | None = None,
    ) -> CacheStatus: ...
