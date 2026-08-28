from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from ferc_elibrary_mcp import config
from ferc_elibrary_mcp.store.local import LocalDocumentStore
from ferc_elibrary_mcp.store.protocol import DocumentStore
from ferc_elibrary_mcp.store.s3_uri import parse_s3_uri

StoreRoot = Union[Path, str, None]


def create_store(root: StoreRoot = None, backend: str | None = None) -> DocumentStore:
    backend_name = (backend or config.STORE_BACKEND).lower()
    root_raw = root
    if root_raw is None:
        root_raw = os.environ.get("FERC_STORE_ROOT") or os.environ.get("FERC_DOWNLOAD_DIR") or ""
    root_str = str(root_raw).strip() if root_raw is not None else ""

    if backend_name == "s3" or root_str.startswith("s3://"):
        from ferc_elibrary_mcp.store.s3 import S3DocumentStore

        uri = root_str if root_str.startswith("s3://") else os.environ.get("FERC_STORE_ROOT", "")
        if not uri or not uri.startswith("s3://"):
            raise ValueError(
                "FERC_STORE_BACKEND=s3 requires FERC_STORE_ROOT=s3://bucket/prefix"
            )
        return S3DocumentStore(parse_s3_uri(uri))
    local_root = root_raw if isinstance(root_raw, Path) else config.resolve_store_root(root_str)
    return LocalDocumentStore(local_root)
