from __future__ import annotations

from pathlib import Path

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.store.local import LocalDocumentStore
from ferc_elibrary_mcp.store.protocol import DocumentStore


def create_store(root: Path | None = None, backend: str | None = None) -> DocumentStore:
    backend_name = (backend or config.STORE_BACKEND).lower()
    if backend_name == "local":
        return LocalDocumentStore(root)
    if backend_name == "s3":
        raise NotImplementedError(
            "S3 store backend is planned for Phase 2. Use FERC_STORE_BACKEND=local."
        )
    raise ValueError(f"Unknown FERC_STORE_BACKEND: {backend_name}")
