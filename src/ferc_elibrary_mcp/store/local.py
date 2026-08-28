from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.store.models import (
    AccessionManifest,
    CacheStatus,
    DocketIndex,
    StoredFile,
    utc_now_iso,
)
from ferc_elibrary_mcp.store.paths import (
    accession_dir,
    docket_index_path,
    legacy_v1_file_path,
    manifest_path,
    safe_name,
    stored_file_path,
)


class LocalDocumentStore:
    """Filesystem-backed document store with atomic manifest writes."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or config.resolve_store_root()).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def backend(self) -> str:
        return "local"

    @property
    def root(self) -> Path:
        return self._root

    def exists(self, docket: str, accession: str, filename: str) -> bool:
        path = self.get(docket, accession, filename)
        return path is not None and path.is_file()

    def accession_cached(self, docket: str, accession: str) -> bool:
        manifest = self.manifest(docket, accession)
        if manifest is not None and manifest.files:
            return True
        directory = accession_dir(self._root, docket, accession)
        if directory.is_dir() and any(directory.iterdir()):
            return True
        legacy = self._root / safe_name(accession)
        return legacy.is_dir() and any(p.is_file() for p in legacy.iterdir())

    def get(self, docket: str, accession: str, filename: str) -> Path | None:
        path = stored_file_path(self._root, docket, accession, filename)
        if path.is_file():
            return path
        legacy = legacy_v1_file_path(self._root, accession, filename)
        if legacy.is_file():
            return legacy
        return None

    def put(
        self,
        docket: str,
        accession: str,
        filename: str,
        body: bytes,
        *,
        file_id: str = "",
        content_type: str = "",
    ) -> Path:
        directory = accession_dir(self._root, docket, accession)
        directory.mkdir(parents=True, exist_ok=True)
        path = stored_file_path(self._root, docket, accession, filename)
        self._atomic_write(path, body)
        manifest = self.manifest(docket, accession) or AccessionManifest(
            accession_number=accession,
            docket_numbers=[docket],
        )
        sha = hashlib.sha256(body).hexdigest()
        stored = next((f for f in manifest.files if f.filename == filename), None)
        if stored is None:
            manifest.files.append(
                StoredFile(
                    filename=filename,
                    file_id=file_id,
                    size_bytes=len(body),
                    sha256=sha,
                    content_type=content_type,
                    fetched_at=utc_now_iso(),
                )
            )
        else:
            stored.file_id = file_id or stored.file_id
            stored.size_bytes = len(body)
            stored.sha256 = sha
            stored.content_type = content_type or stored.content_type
            stored.fetched_at = utc_now_iso()
        manifest.fetch_timestamp = utc_now_iso()
        self.save_manifest(docket, accession, manifest)
        return path

    def list_files(self, docket: str, accession: str) -> list[str]:
        manifest = self.manifest(docket, accession)
        if manifest and manifest.files:
            return [f.filename for f in manifest.files]
        directory = accession_dir(self._root, docket, accession)
        if directory.is_dir():
            return [
                p.name
                for p in directory.iterdir()
                if p.is_file()
                and not p.name.endswith(".extracted.txt")
                and not p.name.endswith(".pages.json")
                and p.name != "manifest.json"
            ]
        legacy = self._root / safe_name(accession)
        if legacy.is_dir():
            return [p.name for p in legacy.iterdir() if p.is_file()]
        return []

    def manifest(self, docket: str, accession: str) -> AccessionManifest | None:
        path = manifest_path(self._root, docket, accession)
        if not path.is_file():
            return None
        return AccessionManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def save_manifest(self, docket: str, accession: str, manifest: AccessionManifest) -> None:
        path = manifest_path(self._root, docket, accession)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, manifest.model_dump_json(indent=2).encode("utf-8"))

    def docket_index(self, docket: str) -> DocketIndex | None:
        path = docket_index_path(self._root, docket)
        if not path.is_file():
            return None
        return DocketIndex.model_validate_json(path.read_text(encoding="utf-8"))

    def update_docket_index(self, docket: str, index: DocketIndex) -> None:
        path = docket_index_path(self._root, docket)
        path.parent.mkdir(parents=True, exist_ok=True)
        index.last_synced_at = utc_now_iso()
        with self._lock(path):
            self._atomic_write(path, index.model_dump_json(indent=2).encode("utf-8"))

    def cache_status(
        self,
        *,
        docket: str | None = None,
        accession: str | None = None,
    ) -> CacheStatus:
        accessions: list[dict] = []
        total_files = 0
        total_bytes = 0
        dockets_root = self._root / "dockets"
        if docket:
            docket_dirs = [dockets_root / safe_name(docket)]
        else:
            docket_dirs = [p for p in dockets_root.iterdir()] if dockets_root.is_dir() else []

        for docket_dir in docket_dirs:
            if not docket_dir.is_dir():
                continue
            docket_name = docket_dir.name
            for acc_dir in sorted(docket_dir.iterdir()):
                if not acc_dir.is_dir() or acc_dir.name == "docket-index.json":
                    continue
                acc = acc_dir.name
                if accession and acc != safe_name(accession):
                    continue
                manifest = self.manifest(docket_name, acc)
                files = manifest.files if manifest else []
                file_count = len(files) or len(self.list_files(docket_name, acc))
                byte_total = sum(f.size_bytes for f in files)
                total_files += file_count
                total_bytes += byte_total
                accessions.append(
                    {
                        "docket_number": docket_name,
                        "accession_number": acc,
                        "file_count": file_count,
                        "total_bytes": byte_total,
                        "cached": True,
                    }
                )

        return CacheStatus(
            backend=self.backend,
            root=str(self._root),
            docket_number=docket,
            accession_number=accession,
            accessions=accessions,
            total_files=total_files,
            total_bytes=total_bytes,
        )

    def _atomic_write(self, path: Path, body: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)

    @contextmanager
    def _lock(self, path: Path):
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
