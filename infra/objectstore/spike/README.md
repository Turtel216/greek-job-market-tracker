# Storage spike

Checks that an S3-compatible server does what the pipeline needs, using plain
boto3 with its default settings. It was used to pick Garage over SeaweedFS and
can be run again when either server is upgraded.

## What it checks

| Check | Needed by |
| --- | --- |
| Put and get a gzipped payload with user metadata | Extract writes payloads |
| Head returns size and a timezone aware `LastModified` | Retention by age |
| Get on a missing key raises `NoSuchKey` | Loader error handling |
| List with `Delimiter="/"` returns run folders | Loader finds the newest complete `run_id` |
| List pages through many keys in order, and `StartAfter` | Loader and retention on big days |
| Multipart upload and download of 12 MB | Large listing pages |
| Delete one object, then delete it again | Retention retries |
| Batch delete with `delete_objects` | Retention job |
| Put with `If-None-Match: *` (optional) | Store side guard for raw objects |
| 200 puts, gets and deletes of 20 KB (optional) | Rough timing |

Optional checks print `WARN` instead of failing the run.

## How to run

Garage, from the main stack:

```sh
make up
set -a; . ./.env; set +a
uv run infra/objectstore/spike/storage_spike.py --endpoint http://127.0.0.1:3900
```

SeaweedFS, from the spike Compose file. It reuses the S3 key from `.env`:

```sh
docker compose --env-file .env -f infra/objectstore/spike/docker-compose.seaweedfs.yml up -d
uv run infra/objectstore/spike/storage_spike.py --endpoint http://127.0.0.1:8333 \
    --region us-east-1 --create-bucket
docker compose --env-file .env -f infra/objectstore/spike/docker-compose.seaweedfs.yml down -v
```

Add `--json results.json` to save the results.

## Results, 2026-10-24

Garage v2.4.1 and SeaweedFS 4.48, both single node in Docker on a laptop,
boto3 1.43.111.

| | Garage | SeaweedFS |
| --- | --- | --- |
| Required checks | all pass | all pass |
| `If-None-Match` on put | ignored, object overwritten | rejected with 412 |
| Put / get per 20 KB object | 2.7 ms / 1.4 ms | 2.1 ms / 1.5 ms |
| Batch delete of 200 keys | 144 ms | 79 ms |
| Memory when idle | about 5 to 8 MB | about 120 to 220 MB |
| Image size | 71 MB | 529 MB |
| Data kept after a restart | yes | yes |
| Bucket and key setup | admin API (`init_bucket.py`) | S3 `CreateBucket` plus an identity file |

Both work with default boto3 settings, including its default request
checksums. Speed is the same for our volumes. Garage wins on memory and
image size, which matters on the 8 GB server, and is already wired into the
stack. Its one gap is that it ignores conditional puts, so raw objects stay
immutable through the key layout (a new `run_id` per run) and not through the
store. Code must not rely on `If-None-Match`.
