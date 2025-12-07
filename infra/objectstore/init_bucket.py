"""Set up the single Garage node, the access key and the raw bucket.

Talks to the Garage admin API, so it only needs the standard library.
Safe to run again: every step checks what already exists first.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

ADMIN_URL = os.environ.get("GARAGE_ADMIN_URL", "http://objectstore:3903")
ADMIN_TOKEN = os.environ["GARAGE_ADMIN_TOKEN"]
ACCESS_KEY_ID = os.environ["S3_ACCESS_KEY_ID"]
SECRET_ACCESS_KEY = os.environ["S3_SECRET_ACCESS_KEY"]
BUCKET = os.environ["S3_BUCKET_RAW"]

# Capacity is only used to spread data between nodes, so with one node
# the exact number does not matter.
NODE_CAPACITY_BYTES = 10 * 1024**3


def call(method, path, body=None, allow_404=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ADMIN_URL + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {ADMIN_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        if allow_404 and err.code in (400, 404):
            return None
        detail = err.read().decode(errors="replace")
        sys.exit(f"{method} {path} failed with {err.code}: {detail}")
    return json.loads(raw) if raw else None


def wait_for_admin_api():
    for _ in range(30):
        try:
            return call("GET", "/v2/GetClusterStatus")
        except urllib.error.URLError:
            time.sleep(2)
    sys.exit("Garage admin API did not come up")


def ensure_layout(status):
    layout = call("GET", "/v2/GetClusterLayout")
    if layout["roles"]:
        print("Layout already set")
        return

    node_id = status["nodes"][0]["id"]
    role = {"id": node_id, "zone": "dc1", "capacity": NODE_CAPACITY_BYTES, "tags": []}
    call("POST", "/v2/UpdateClusterLayout", {"roles": [role]})
    call("POST", "/v2/ApplyClusterLayout", {"version": layout["version"] + 1})
    print(f"Assigned layout to node {node_id[:16]}")


def ensure_key():
    if call("GET", f"/v2/GetKeyInfo?id={ACCESS_KEY_ID}", allow_404=True):
        print("Access key already exists")
        return

    body = {
        "accessKeyId": ACCESS_KEY_ID,
        "secretAccessKey": SECRET_ACCESS_KEY,
        "name": "jobtracker",
    }
    call("POST", "/v2/ImportKey", body)
    print("Imported access key")


def ensure_bucket():
    bucket = call("GET", f"/v2/GetBucketInfo?globalAlias={BUCKET}", allow_404=True)
    if bucket:
        print(f"Bucket {BUCKET} already exists")
    else:
        bucket = call("POST", "/v2/CreateBucket", {"globalAlias": BUCKET})
        print(f"Created bucket {BUCKET}")

    perms = {"read": True, "write": True, "owner": True}
    body = {"bucketId": bucket["id"], "accessKeyId": ACCESS_KEY_ID, "permissions": perms}
    call("POST", "/v2/AllowBucketKey", body)


def main():
    status = wait_for_admin_api()
    ensure_layout(status)
    ensure_key()
    ensure_bucket()
    print("Object store is ready")


if __name__ == "__main__":
    main()
