# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3==1.43.111"]
# ///
"""Storage spike: check that an S3-compatible server does what the pipeline
needs, using plain boto3 with its default settings.

Covers put, get, list and delete, plus the S3 features the extract, load and
maintenance steps rely on. Every object is written under a random spike/
prefix and removed at the end.

Run against the store in .env (Garage from `make up`):

    uv run infra/objectstore/spike/storage_spike.py

Or against another server:

    uv run infra/objectstore/spike/storage_spike.py --endpoint http://127.0.0.1:8333 \\
        --region us-east-1 --create-bucket
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import time
import uuid

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

RESULTS = []


def check(name, required=True):
    """Run one check and record whether it passed."""

    def wrap(fn):
        def run(*args):
            started = time.perf_counter()
            try:
                detail = fn(*args) or ""
                ok = True
            except Exception as err:
                detail = f"{type(err).__name__}: {err}"
                ok = False
            elapsed = time.perf_counter() - started
            RESULTS.append((name, ok, required, elapsed, detail))

        return run

    return wrap


def error_code(err):
    return err.response.get("Error", {}).get("Code", "")


def gzip_json(payload):
    return gzip.compress(json.dumps(payload).encode())


def key_exists(s3, bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as err:
        if error_code(err) in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def list_all_keys(s3, bucket, prefix, page_size=1000):
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(
        Bucket=bucket, Prefix=prefix, PaginationConfig={"PageSize": page_size}
    ):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


@check("put and get a gzipped payload with metadata")
def check_put_get(s3, bucket, root):
    key = f"{root}raw/greenhouse/2026-10-24/run-a/detail/1001.json.gz"
    body = gzip_json({"id": 1001, "title": "Data Engineer, Αθήνα"})
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/gzip",
        Metadata={"parser-version": "1", "source": "greenhouse"},
    )
    obj = s3.get_object(Bucket=bucket, Key=key)
    got = obj["Body"].read()
    assert hashlib.sha256(got).digest() == hashlib.sha256(body).digest(), "body differs"
    assert obj["Metadata"] == {"parser-version": "1", "source": "greenhouse"}, obj["Metadata"]
    assert obj["ContentType"] == "application/gzip", obj["ContentType"]
    assert json.loads(gzip.decompress(got))["title"] == "Data Engineer, Αθήνα"
    return f"{len(body)} bytes round trip, metadata kept"


@check("head returns size and last modified")
def check_head(s3, bucket, root):
    key = f"{root}raw/greenhouse/2026-10-24/run-a/detail/1001.json.gz"
    head = s3.head_object(Bucket=bucket, Key=key)
    assert head["ContentLength"] > 0
    assert head["LastModified"].tzinfo is not None, "LastModified has no timezone"
    return f"LastModified {head['LastModified'].isoformat()}"


@check("get on a missing key raises NoSuchKey")
def check_missing_key(s3, bucket, root):
    try:
        s3.get_object(Bucket=bucket, Key=f"{root}does/not/exist.json.gz")
    except ClientError as err:
        assert error_code(err) == "NoSuchKey", error_code(err)
        return "NoSuchKey"
    raise AssertionError("get on a missing key did not fail")


@check("list run folders with a delimiter")
def check_list_delimiter(s3, bucket, root):
    # Same layout the pipeline uses: raw/<source>/<run_date>/<run_id>/
    day = f"{root}raw/greenhouse/2026-10-25/"
    for run_id in ("run-1", "run-2", "run-3"):
        s3.put_object(Bucket=bucket, Key=f"{day}{run_id}/listing/page-0001.json.gz", Body=b"x")
    s3.put_object(Bucket=bucket, Key=f"{day}run-2/_manifest.json", Body=b"{}")

    resp = s3.list_objects_v2(Bucket=bucket, Prefix=day, Delimiter="/")
    prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    expected = [f"{day}run-1/", f"{day}run-2/", f"{day}run-3/"]
    assert prefixes == expected, prefixes
    assert not resp.get("Contents"), "objects leaked past the delimiter"

    # The loader picks runs that have a manifest
    complete = [p for p in prefixes if key_exists(s3, bucket, p + "_manifest.json")]
    assert complete == [f"{day}run-2/"], complete
    return "3 run folders, 1 complete"


@check("list pages through many keys in order")
def check_list_pagination(s3, bucket, root):
    prefix = f"{root}raw/greenhouse/2026-10-26/run-a/detail/"
    expected = [f"{prefix}{i:04d}.json.gz" for i in range(25)]
    for key in expected:
        s3.put_object(Bucket=bucket, Key=key, Body=b"x")
    keys = list_all_keys(s3, bucket, prefix, page_size=10)
    assert keys == expected, f"got {len(keys)} keys"
    return "25 keys over 3 pages"


@check("list with StartAfter skips earlier keys")
def check_start_after(s3, bucket, root):
    prefix = f"{root}raw/greenhouse/2026-10-26/run-a/detail/"
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, StartAfter=f"{prefix}0019.json.gz")
    keys = [obj["Key"] for obj in resp.get("Contents", [])]
    assert keys == [f"{prefix}{i:04d}.json.gz" for i in range(20, 25)], keys
    return "5 keys after 0019"


@check("multipart upload of a 12 MB object")
def check_multipart(s3, bucket, root):
    key = f"{root}big/listing.html.gz"
    body = os.urandom(12 * 1024 * 1024)
    part = 5 * 1024 * 1024
    config = TransferConfig(multipart_threshold=part, multipart_chunksize=part)
    s3.upload_fileobj(io.BytesIO(body), bucket, key, Config=config)
    out = io.BytesIO()
    s3.download_fileobj(bucket, key, out, Config=config)
    assert hashlib.sha256(out.getvalue()).digest() == hashlib.sha256(body).digest()
    return "3 parts, body matches"


@check("put with If-None-Match refuses to overwrite", required=False)
def check_if_none_match(s3, bucket, root):
    # Nice to have: lets the store itself guard raw objects against overwrite
    key = f"{root}raw/greenhouse/2026-10-24/run-a/_manifest.json"
    s3.put_object(Bucket=bucket, Key=key, Body=b"{}", IfNoneMatch="*")
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=b"{changed}", IfNoneMatch="*")
    except ClientError as err:
        assert error_code(err) in ("PreconditionFailed", "412"), error_code(err)
        return "second put rejected"
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    raise AssertionError(f"second put was accepted, object is now {body!r}")


@check("delete one object")
def check_delete_one(s3, bucket, root):
    key = f"{root}raw/greenhouse/2026-10-24/run-a/detail/1001.json.gz"
    s3.delete_object(Bucket=bucket, Key=key)
    assert not key_exists(s3, bucket, key), "object still there"
    # Deleting again must not fail, retention may retry
    s3.delete_object(Bucket=bucket, Key=key)
    return "gone, second delete is a no-op"


@check("batch delete everything left")
def check_delete_batch(s3, bucket, root):
    keys = list_all_keys(s3, bucket, root)
    for start in range(0, len(keys), 1000):
        chunk = [{"Key": k} for k in keys[start : start + 1000]]
        resp = s3.delete_objects(Bucket=bucket, Delete={"Objects": chunk, "Quiet": True})
        assert not resp.get("Errors"), resp["Errors"]
    left = list_all_keys(s3, bucket, root)
    assert not left, f"{len(left)} keys left"
    return f"{len(keys)} keys deleted"


@check("timing: 200 puts, gets and deletes of 20 KB", required=False)
def check_timing(s3, bucket, root):
    prefix = f"{root}timing/"
    body = os.urandom(20 * 1024)
    keys = [f"{prefix}{i:04d}.json.gz" for i in range(200)]

    started = time.perf_counter()
    for key in keys:
        s3.put_object(Bucket=bucket, Key=key, Body=body)
    put_s = time.perf_counter() - started

    started = time.perf_counter()
    for key in keys:
        s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    get_s = time.perf_counter() - started

    started = time.perf_counter()
    assert len(list_all_keys(s3, bucket, prefix)) == 200
    list_s = time.perf_counter() - started

    started = time.perf_counter()
    s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})
    delete_s = time.perf_counter() - started
    return (
        f"put {put_s / 200 * 1000:.1f} ms, get {get_s / 200 * 1000:.1f} ms per object, "
        f"list {list_s * 1000:.0f} ms, batch delete {delete_s * 1000:.0f} ms"
    )


def make_client(args):
    # Default boto3 settings on purpose, apart from path style addressing,
    # so the result matches what the ingestion code will use.
    return boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        region_name=args.region,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        config=Config(s3={"addressing_style": "path"}),
    )


def ensure_bucket(s3, bucket, create):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        if not create:
            raise
        s3.create_bucket(Bucket=bucket)
        print(f"Created bucket {bucket}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", default=os.environ.get("S3_ENDPOINT_URL"))
    parser.add_argument("--region", default=os.environ.get("S3_REGION", "us-east-1"))
    parser.add_argument("--access-key", default=os.environ.get("S3_ACCESS_KEY_ID"))
    parser.add_argument("--secret-key", default=os.environ.get("S3_SECRET_ACCESS_KEY"))
    parser.add_argument("--bucket", default=os.environ.get("S3_BUCKET_RAW", "jobtracker-raw"))
    parser.add_argument("--create-bucket", action="store_true", help="create it if missing")
    parser.add_argument("--json", help="also write the results to this file")
    args = parser.parse_args()
    missing = [n for n in ("endpoint", "access_key", "secret_key") if not getattr(args, n)]
    if missing:
        parser.error("missing " + ", ".join(missing) + " (flag or S3_* variable)")
    return args


def main():
    args = parse_args()
    s3 = make_client(args)
    ensure_bucket(s3, args.bucket, args.create_bucket)
    root = f"spike/{uuid.uuid4().hex}/"

    checks = [
        check_put_get,
        check_head,
        check_missing_key,
        check_list_delimiter,
        check_list_pagination,
        check_start_after,
        check_multipart,
        check_if_none_match,
        check_delete_one,
        check_delete_batch,
        check_timing,
    ]
    for run in checks:
        run(s3, args.bucket, root)

    # Clean up even when a check failed half way
    leftover = list_all_keys(s3, args.bucket, root)
    if leftover:
        s3.delete_objects(
            Bucket=args.bucket,
            Delete={"Objects": [{"Key": k} for k in leftover], "Quiet": True},
        )

    print(f"Endpoint {args.endpoint}, bucket {args.bucket}, boto3 {boto3.__version__}\n")
    for name, ok, required, elapsed, detail in RESULTS:
        status = "PASS" if ok else ("FAIL" if required else "WARN")
        print(f"{status}  {name} ({elapsed:.2f} s)")
        if detail:
            print(f"      {detail}")

    if args.json:
        rows = [
            {"check": n, "ok": ok, "required": r, "seconds": round(e, 3), "detail": d}
            for n, ok, r, e, d in RESULTS
        ]
        with open(args.json, "w") as fh:
            json.dump({"endpoint": args.endpoint, "results": rows}, fh, indent=2)

    failed = [name for name, ok, required, _, _ in RESULTS if required and not ok]
    if failed:
        sys.exit(f"\n{len(failed)} required check(s) failed")
    print("\nAll required checks passed")


if __name__ == "__main__":
    main()
