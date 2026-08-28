from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ferc_elibrary_mcp.store.local import LocalDocumentStore
from ferc_elibrary_mcp.store.models import AccessionManifest, DocketIndex, DocketIndexEntry


@pytest.fixture
def store(tmp_path: Path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path)


def test_put_and_exists(store: LocalDocumentStore) -> None:
    path = store.put("CP21-470", "20201119-5202", "comment.pdf", b"%PDF-test", file_id="1")
    assert path.is_file()
    assert store.exists("CP21-470", "20201119-5202", "comment.pdf")


def test_manifest_roundtrip(store: LocalDocumentStore) -> None:
    store.put("ER26-3176", "20201119-5202", "order.pdf", b"%PDF", content_type="application/pdf")
    manifest = store.manifest("ER26-3176", "20201119-5202")
    assert manifest is not None
    assert manifest.accession_number == "20201119-5202"
    assert len(manifest.files) == 1
    assert manifest.files[0].size_bytes == 4


def test_docket_index_atomic_update(store: LocalDocumentStore) -> None:
    index = DocketIndex(
        docket_number="CP21-470",
        accessions=[DocketIndexEntry(accession_number="20201119-5202", filed_date="01/01/2026")],
    )
    store.update_docket_index("CP21-470", index)
    loaded = store.docket_index("CP21-470")
    assert loaded is not None
    assert loaded.accessions[0].accession_number == "20201119-5202"


def test_legacy_v1_path_promotion(store: LocalDocumentStore, tmp_path: Path) -> None:
    legacy = tmp_path / "20201119-5202" / "legacy.pdf"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    assert store.get("CP21-470", "20201119-5202", "legacy.pdf") == legacy


@pytest.mark.asyncio
async def test_concurrent_manifest_writes(store: LocalDocumentStore) -> None:
    async def writer(suffix: str) -> None:
        manifest = AccessionManifest(
            accession_number="20201119-5202",
            docket_numbers=["CP21-470"],
            description=suffix,
        )
        store.save_manifest("CP21-470", "20201119-5202", manifest)

    await asyncio.gather(*(writer(str(i)) for i in range(20)))
    manifest = store.manifest("CP21-470", "20201119-5202")
    assert manifest is not None
