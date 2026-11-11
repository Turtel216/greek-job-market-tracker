"""Nightly pg_dump backups of the warehouse and metabase_app databases.

Each database gets a folder of its own, with one custom format dump per
run_date:

    <folder>/<database>/<database>-<run_date>.dump

pg_dump writes to a .partial file first, and pg_restore reads its table of
contents back, before the file is renamed. So a dump that failed half way
never looks like a good one. Running the backup again for the same
run_date replaces that day's dump.

A dump smaller than half the one before it fails the backup, since that
usually means lost data rather than a smaller database. The new dump is
kept, but nothing is pruned that night, so every older dump is still there.

Pruning keeps the 14 newest dumps and the newest dump of each of the 8
newest ISO weeks.

pg_dump connects as the read-only backup role, with POSTGRES_HOST,
POSTGRES_PORT and BACKUP_PASSWORD (and BACKUP_USER, which defaults to
backup).
"""

import os
import re
import subprocess
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path

import structlog

from jobtracker_ingest.core.models import Model

KEEP_DAILY = 14
KEEP_WEEKLY = 8
DUMP_SUFFIX = ".dump"
PARTIAL_SUFFIX = ".partial"
# Keeps the error message short enough for an alert
MAX_STDERR_CHARS = 1500

# Writes a complete dump of the database to the path
Dump = Callable[[str, Path], None]


class BackupError(Exception):
    """The dump failed, or it looks wrong."""


class BackupResult(Model):
    database: str
    path: str
    size: int
    # Size of the newest dump of an earlier run_date, if any
    previous_size: int | None
    # File names of the dumps pruned this run
    pruned: list[str]


def dump_name(database: str, run_date: date) -> str:
    return f"{database}-{run_date.isoformat()}{DUMP_SUFFIX}"


def dump_date(database: str, name: str) -> date | None:
    """The run_date of a dump file name, or None for any other file."""
    match = re.fullmatch(
        re.escape(database) + r"-(\d{4}-\d{2}-\d{2})" + re.escape(DUMP_SUFFIX), name
    )
    if match is None:
        return None
    try:
        return date.fromisoformat(match[1])
    except ValueError:
        return None


def stored_dumps(folder: Path, database: str) -> dict[date, Path]:
    """The dumps of one database in its folder, by run_date."""
    dumps = {}
    for path in folder.iterdir():
        day = dump_date(database, path.name)
        if day is not None and path.is_file():
            dumps[day] = path
    return dumps


def dumps_to_keep(
    dates: Iterable[date], daily: int = KEEP_DAILY, weekly: int = KEEP_WEEKLY
) -> set[date]:
    """The newest daily dates, plus the newest date of each of the newest weekly ISO weeks."""
    newest_first = sorted(set(dates), reverse=True)
    keep = set(newest_first[:daily])
    # Newest first, so the first date seen in a week is its newest
    weeks: dict[tuple[int, int], date] = {}
    for day in newest_first:
        year, week, _ = day.isocalendar()
        weeks.setdefault((year, week), day)
    keep.update(list(weeks.values())[:weekly])
    return keep


def prune_dumps(
    folder: Path, database: str, daily: int = KEEP_DAILY, weekly: int = KEEP_WEEKLY
) -> list[str]:
    """Delete the dumps that dumps_to_keep leaves out and return their names."""
    dumps = stored_dumps(folder, database)
    keep = dumps_to_keep(dumps, daily, weekly)
    pruned = []
    for day in sorted(dumps):
        if day not in keep:
            dumps[day].unlink()
            pruned.append(dumps[day].name)
    return pruned


def pg_dump_command(database: str, path: Path) -> list[str]:
    return [
        "pg_dump",
        "--format=custom",
        "--no-password",
        "--host",
        os.environ.get("POSTGRES_HOST", "localhost"),
        "--port",
        os.environ.get("POSTGRES_PORT", "5432"),
        "--username",
        os.environ.get("BACKUP_USER", "backup"),
        "--dbname",
        database,
        "--file",
        str(path),
    ]


def run_checked(command: list[str], env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip()[-MAX_STDERR_CHARS:]
        raise BackupError(f"{command[0]} exited with {result.returncode}: {stderr}")


def pg_dump(database: str, path: Path) -> None:
    """Dump the database with pg_dump, then check pg_restore can read it."""
    password = os.environ.get("BACKUP_PASSWORD")
    if not password:
        raise BackupError("BACKUP_PASSWORD is not set")
    run_checked(pg_dump_command(database, path), env={**os.environ, "PGPASSWORD": password})
    run_checked(["pg_restore", "--list", str(path)])


def backup_database(
    database: str,
    run_date: date,
    folder: Path,
    dump: Dump = pg_dump,
    daily: int = KEEP_DAILY,
    weekly: int = KEEP_WEEKLY,
) -> BackupResult:
    log = structlog.get_logger(__name__)
    target_dir = folder / database
    try:
        target_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    except OSError as err:
        raise BackupError(f"cannot create {target_dir}: {err}") from err
    if not os.access(target_dir, os.W_OK):
        raise BackupError(f"{target_dir} is not writable by this user")

    earlier = {d: p for d, p in stored_dumps(target_dir, database).items() if d < run_date}
    previous = earlier[max(earlier)] if earlier else None
    previous_size = previous.stat().st_size if previous else None

    target = target_dir / dump_name(database, run_date)
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        dump(database, partial)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    # Only the owner and its group may read a dump
    partial.chmod(0o640)
    partial.replace(target)
    size = target.stat().st_size

    if previous is not None and previous_size is not None and size < previous_size / 2:
        log.error(
            "backup_too_small",
            database=database,
            path=str(target),
            size=size,
            previous=previous.name,
            previous_size=previous_size,
        )
        raise BackupError(
            f"{target.name} is {size} bytes, under half of {previous.name} "
            f"({previous_size} bytes). Kept it and pruned nothing."
        )

    pruned = prune_dumps(target_dir, database, daily, weekly)
    result = BackupResult(
        database=database,
        path=str(target),
        size=size,
        previous_size=previous_size,
        pruned=pruned,
    )
    log.info("backup_finished", **result.model_dump())
    return result
