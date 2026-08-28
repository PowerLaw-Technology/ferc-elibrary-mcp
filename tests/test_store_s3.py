"""Tests for S3 document store (mocked boto3, no real AWS)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ferc_elibrary_mcp.store.s3 import S3DocumentStore
from ferc_elibrary_mcp.store.s3_uri import parse_s3_uri


@pytest.fixture
def mock_s3(tmp_path: Path):
    client = MagicMock()
    objects: dict[str, bytes] = {}

    def put_object(Bucket, Key, Body, **kwargs):
        objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def upload_file(path, bucket, key):
        objects[key] = Path(path).read_bytes()

    def download_file(bucket, key, path):
        Path(path).write_bytes(objects[key])

    def head_object(Bucket, Key):
        if Key not in objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    client.put_object = put_object
    client.upload_file = upload_file
    client.download_file = download_file
    client.head_object = head_object
    client.list_objects_v2 = MagicMock(return_value={"Contents": []})

    with patch("ferc_elibrary_mcp.store.s3._s3_client", return_value=client):
        store = S3DocumentStore(parse_s3_uri("s3://test-bucket/ferc"), cache_dir=tmp_path)
        yield store, objects


def test_s3_put_and_exists(mock_s3) -> None:
    store, objects = mock_s3
    store.put("CP21-470", "20201119-5202", "filing.pdf", b"%PDF", content_type="application/pdf")
    assert store.exists("CP21-470", "20201119-5202", "filing.pdf")
    assert any(k.endswith("filing.pdf") for k in objects)


def test_s3_cache_status_shows_uri(mock_s3) -> None:
    store, _ = mock_s3
    status = store.cache_status()
    assert status.backend == "s3"
    assert status.root == "s3://test-bucket/ferc"
