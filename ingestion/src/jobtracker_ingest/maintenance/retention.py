"""Delete raw payloads once they are past the retention period.

Raw payloads may still hold the emails, phone numbers and names that the
loader scrubs before Postgres, so they are kept for 30 days only. The
run_date in the key (raw/<source>/<run_date>/<run_id>/...) decides an
object's age, not the time it was written. That works the same on any
S3-compatible store.

A stored detail can outlive its run: when extract does not fetch a
posting's detail, the loader reuses the last one named in
ops.posting_fingerprints. So the detail keys of postings seen within the
retention period are kept even when their run is older. A posting that was
not seen for that long fetches its detail again if it comes back, since
extract fetches any detail older than 14 days.

Keys that do not follow the layout are left alone and logged.
"""

import re
from collections import Counter
from datetime import date, timedelta

import structlog

from jobtracker_ingest.core.models import Model
from jobtracker_ingest.core.storage import ObjectStore
from jobtracker_ingest.loader.warehouse import Warehouse

KEEP_DAYS = 30
RAW_PREFIX = "raw/"

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class PruneResult(Model):
    # Objects with a run_date before this are expired
    cutoff: date
    deleted: int
    # Expired, but still the stored detail of a posting seen recently
    kept_in_use: int
    # Keys that do not follow the raw layout
    unknown: int
    # Deleted objects per source and run_date, such as "kariera/2026-10-01"
    deleted_days: dict[str, int]


def key_run_date(key: str) -> date | None:
    """The run_date in raw/<source>/<run_date>/<run_id>/<name>, or None."""
    parts = key.split("/")
    if len(parts) < 5 or parts[0] != "raw" or not DATE.fullmatch(parts[2]):
        return None
    try:
        return date.fromisoformat(parts[2])
    except ValueError:
        return None


def cutoff_for(today: date, keep_days: int = KEEP_DAYS) -> date:
    """The oldest run_date that is kept."""
    return today - timedelta(days=keep_days)


def prune_raw(
    store: ObjectStore,
    warehouse: Warehouse,
    today: date,
    keep_days: int = KEEP_DAYS,
    dry_run: bool = False,
) -> PruneResult:
    """Delete every raw object older than keep_days, except details in use.

    With dry_run nothing is deleted, but the result says what would be.
    """
    if keep_days < 1:
        raise ValueError(f"keep_days must be at least 1, not {keep_days}")
    log = structlog.get_logger(__name__)
    cutoff = cutoff_for(today, keep_days)
    # Read before listing, so a key counts as in use if any posting seen
    # since the cutoff still points at it
    in_use = warehouse.detail_keys_in_use(cutoff)

    expired: list[str] = []
    days: Counter[str] = Counter()
    kept_in_use = 0
    unknown = 0
    for key in store.list_keys(RAW_PREFIX):
        run_date = key_run_date(key)
        if run_date is None:
            unknown += 1
            log.warning("raw_key_unknown", key=key)
            continue
        if run_date >= cutoff:
            continue
        if key in in_use:
            kept_in_use += 1
            continue
        expired.append(key)
        days[f"{key.split('/')[1]}/{run_date.isoformat()}"] += 1

    if not dry_run:
        store.delete(expired)

    result = PruneResult(
        cutoff=cutoff,
        deleted=len(expired),
        kept_in_use=kept_in_use,
        unknown=unknown,
        deleted_days=dict(sorted(days.items())),
    )
    log.info(
        "raw_pruned" if not dry_run else "raw_prune_dry_run",
        cutoff=cutoff.isoformat(),
        deleted=result.deleted,
        kept_in_use=result.kept_in_use,
        unknown=result.unknown,
        deleted_days=result.deleted_days,
    )
    return result
