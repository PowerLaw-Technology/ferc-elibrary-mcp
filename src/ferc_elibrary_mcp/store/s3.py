from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.store.local import LocalDocumentStore
from ferc_elibrary_mcp.store.models import AccessionManifest, CacheStatus, DocketIndex
from ferc_elibrary_mcp.store.paths import (
    accession_dir,
    docket_index_path,
    manifest_path,
    safe_name,
    stored_file_path,
)
from ferc_elibrary_mcp.store.s3_uri import S3Location, object_key, parse_s3_uri

def _client_error() -> type[Exception]:
    try:
        from botocore.exceptions import ClientError
    except ImportError as exc:
        raise ImportError(
            "S3 store requires boto3. Install with: uv sync --extra s3"
        ) from exc
    return ClientError


def _s3_client() -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "S3 store requires boto3. Install with: uv sync --extra s3"
        ) from exc
    return boto3.client("s3")


class S3DocumentStore:
    """S3-backed store with a local disk cache for Path-compatible reads."""

    def __init__(
        self,
        location: S3Location,
        *,
        cache_dir: Path | None = None,
    ) -> None:
        self._location = location
        self._client = _s3_client()
        cache_root = cache_dir or config.resolve_s3_cache_dir(location.uri)
        self._local = LocalDocumentStore(cache_root)

    @property
    def backend(self) -> str:
        return "s3"

    @property
    def root(self) -> Path:
        return self._local.root

    @property
    def root_uri(self) -> str:
        return self._location.uri

    def _key(self, relative: str) -> str:
        return object_key(self._location, relative)

    def _relative_key(self, docket: str, accession: str, filename: str) -> str:
        path = stored_file_path(Path("."), docket, accession, filename)
        return str(path).lstrip("./")

    def _upload_file(self, local_path: Path, key: str) -> None:
        if not local_path.is_file():
            return
        self._client.upload_file(str(local_path), self._location.bucket, key)

    def _download_file(self, key: str, local_path: Path) -> bool:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self._location.bucket, key, str(local_path))
        except _client_error() as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return local_path.is_file()

    def sync_file(self, local_path: Path) -> None:
        """Upload a file already written under the local cache to S3."""
        try:
            relative = local_path.resolve().relative_to(self._local.root.resolve())
        except ValueError:
            return
        self._upload_file(local_path, self._key(str(relative).replace("\\", "/")))

    def exists(self, docket: str, accession: str, filename: str) -> bool:
        if self._local.exists(docket, accession, filename):
            return True
        key = self._key(self._relative_key(docket, accession, filename))
        try:
            self._client.head_object(Bucket=self._location.bucket, Key=key)
            return True
        except _client_error() as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def accession_cached(self, docket: str, accession: str) -> bool:
        if self._local.accession_cached(docket, accession):
            return True
        prefix = self._key(f"dockets/{safe_name(docket)}/{safe_name(accession)}/")
        response = self._client.list_objects_v2(
            Bucket=self._location.bucket,
            Prefix=prefix,
            MaxKeys=1,
        )
        return bool(response.get("Contents"))

    def get(self, docket: str, accession: str, filename: str) -> Path | None:
        cached = self._local.get(docket, accession, filename)
        if cached is not None:
            return cached
        local_path = stored_file_path(self._local.root, docket, accession, filename)
        key = self._key(self._relative_key(docket, accession, filename))
        if self._download_file(key, local_path):
            return local_path
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
        path = self._local.put(
            docket,
            accession,
            filename,
            body,
            file_id=file_id,
            content_type=content_type,
        )
        key = self._key(self._relative_key(docket, accession, filename))
        self._client.put_object(
            Bucket=self._location.bucket,
            Key=key,
            Body=body,
            ContentType=content_type or "application/octet-stream",
        )
        manifest = self.manifest(docket, accession)
        if manifest is not None:
            self._upload_manifest(docket, accession, manifest)
        return path

    def list_files(self, docket: str, accession: str) -> list[str]:
        return self._local.list_files(docket, accession)

    def manifest(self, docket: str, accession: str) -> AccessionManifest | None:
        local = self._local.manifest(docket, accession)
        if local is not None:
            return local
        rel = str(manifest_path(Path("."), docket, accession)).lstrip("./")
        local_path = manifest_path(self._local.root, docket, accession)
        key = self._key(rel)
        if not self._download_file(key, local_path):
            return None
        return self._local.manifest(docket, accession)

    def _upload_manifest(self, docket: str, accession: str, manifest: AccessionManifest) -> None:
        rel = str(manifest_path(Path("."), docket, accession)).lstrip("./")
        local_path = manifest_path(self._local.root, docket, accession)
        self._upload_file(local_path, self._key(rel))

    def save_manifest(self, docket: str, accession: str, manifest: AccessionManifest) -> None:
        self._local.save_manifest(docket, accession, manifest)
        self._upload_manifest(docket, accession, manifest)

    def docket_index(self, docket: str) -> DocketIndex | None:
        local = self._local.docket_index(docket)
        if local is not None:
            return local
        rel = str(docket_index_path(Path("."), docket)).lstrip("./")
        local_path = docket_index_path(self._local.root, docket)
        if not self._download_file(self._key(rel), local_path):
            return None
        return self._local.docket_index(docket)

    def update_docket_index(self, docket: str, index: DocketIndex) -> None:
        self._local.update_docket_index(docket, index)
        rel = str(docket_index_path(Path("."), docket)).lstrip("./")
        self._upload_file(docket_index_path(self._local.root, docket), self._key(rel))

    def cache_status(
        self,
        *,
        docket: str | None = None,
        accession: str | None = None,
    ) -> CacheStatus:
        status = self._local.cache_status(docket=docket, accession=accession)
        return status.model_copy(update={"backend": self.backend, "root": self.root_uri})
