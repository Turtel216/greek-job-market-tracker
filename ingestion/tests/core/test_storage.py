import gzip
import io
from datetime import date

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from jobtracker_ingest.core.storage import (
    MemoryObjectStore,
    S3ObjectStore,
    gzip_bytes,
    run_prefix,
)

BUCKET = "jobtracker-raw"


@pytest.fixture
def s3() -> S3ObjectStore:
    client = boto3.client(
        "s3",
        endpoint_url="http://objectstore:3900",
        region_name="garage",
        aws_access_key_id="GK000000000000000000000000",
        aws_secret_access_key="test",
    )
    return S3ObjectStore(client, BUCKET)


def test_run_prefix() -> None:
    assert run_prefix("greenhouse", date(2026, 10, 25), "r1") == "raw/greenhouse/2026-10-25/r1/"


def test_gzip_is_deterministic() -> None:
    assert gzip_bytes(b"payload") == gzip_bytes(b"payload")
    assert gzip.decompress(gzip_bytes(b"payload")) == b"payload"


def test_s3_put(s3: S3ObjectStore) -> None:
    with Stubber(s3.client) as stub:
        stub.add_response(
            "put_object",
            {},
            {"Bucket": BUCKET, "Key": "raw/a.json", "Body": b"{}", "ContentType": "x/y"},
        )
        s3.put("raw/a.json", b"{}", "x/y")
        stub.assert_no_pending_responses()


def test_s3_get(s3: S3ObjectStore) -> None:
    body = StreamingBody(io.BytesIO(b"{}"), 2)
    with Stubber(s3.client) as stub:
        stub.add_response("get_object", {"Body": body}, {"Bucket": BUCKET, "Key": "raw/a.json"})
        assert s3.get("raw/a.json") == b"{}"


def test_s3_list_keys_pages_through_results(s3: S3ObjectStore) -> None:
    with Stubber(s3.client) as stub:
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "raw/a"}], "IsTruncated": True, "NextContinuationToken": "t"},
            {"Bucket": BUCKET, "Prefix": "raw/"},
        )
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "raw/b"}], "IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": "raw/", "ContinuationToken": "t"},
        )
        stub.add_response(
            "list_objects_v2", {"IsTruncated": False}, {"Bucket": BUCKET, "Prefix": "x/"}
        )
        assert list(s3.list_keys("raw/")) == ["raw/a", "raw/b"]
        assert list(s3.list_keys("x/")) == []


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://objectstore:3900")
    monkeypatch.setenv("S3_REGION", "garage")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "GK000000000000000000000000")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("S3_BUCKET_RAW", BUCKET)
    store = S3ObjectStore.from_env()
    assert store.bucket == BUCKET
    assert store.client.meta.endpoint_url == "http://objectstore:3900"


def test_memory_store() -> None:
    store = MemoryObjectStore()
    store.put("raw/b", b"2", "x/y")
    store.put("raw/a", b"1", "x/y")
    store.put("other", b"3", "x/y")
    assert store.get("raw/a") == b"1"
    assert list(store.list_keys("raw/")) == ["raw/a", "raw/b"]
    assert store.put_order == ["raw/b", "raw/a", "other"]
