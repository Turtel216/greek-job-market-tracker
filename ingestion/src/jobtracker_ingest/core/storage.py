"""Object store access for raw payloads.

Code talks to storage only through the S3 API, so any S3-compatible server
works. Keys follow the layout in the design doc:

    raw/<source>/<run_date>/<run_id>/
      _manifest.json
      listing/page-0001.json.gz
      detail/<source_job_id>.json.gz

MemoryObjectStore does the same in memory for tests.
"""

import gzip
import os
from collections.abc import Iterator
from datetime import date
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

MANIFEST_NAME = "_manifest.json"


def run_prefix(source: str, run_date: date, run_id: str) -> str:
    return f"raw/{source}/{run_date.isoformat()}/{run_id}/"


def gzip_bytes(data: bytes) -> bytes:
    # mtime=0 so the same payload always gives the same bytes
    return gzip.compress(data, mtime=0)


class ObjectStore(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def list_keys(self, prefix: str) -> Iterator[str]: ...


class S3ObjectStore:
    def __init__(self, client: "S3Client", bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_env(cls) -> "S3ObjectStore":
        client = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            region_name=os.environ.get("S3_REGION"),
            aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
            # Garage and most self-hosted servers need path style addressing
            config=Config(s3={"addressing_style": "path"}),
        )
        return cls(client, os.environ["S3_BUCKET_RAW"])

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)

    def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def list_keys(self, prefix: str) -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]


class MemoryObjectStore:
    """Keeps objects in a dict. Records the order of puts for tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.put_order: list[str] = []

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = body
        self.content_types[key] = content_type
        self.put_order.append(key)

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def list_keys(self, prefix: str) -> Iterator[str]:
        yield from sorted(k for k in self.objects if k.startswith(prefix))
