import os
import stat
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from jobtracker_ingest.maintenance import backup
from jobtracker_ingest.maintenance.backup import (
    BackupError,
    backup_database,
    dump_date,
    dumps_to_keep,
    pg_dump,
    pg_dump_command,
)

DAY = date(2026, 11, 9)


def fake_dump(size: int) -> backup.Dump:
    def dump(database: str, path: Path) -> None:
        path.write_bytes(b"x" * size)

    return dump


def write_dump(folder: Path, day: date, size: int = 100, database: str = "warehouse") -> Path:
    path = folder / database / f"{database}-{day.isoformat()}.dump"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def names(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("warehouse-2026-11-09.dump", date(2026, 11, 9)),
        ("warehouse-2026-11-09.dump.partial", None),
        ("warehouse-2026-02-30.dump", None),
        ("metabase_app-2026-11-09.dump", None),
        ("warehouse-latest.dump", None),
    ],
)
def test_dump_date(name: str, expected: date | None) -> None:
    assert dump_date("warehouse", name) == expected


def test_keeps_14_daily_and_8_weekly_dumps() -> None:
    # One dump a day for 70 days, the newest on Monday 2026-11-09
    dates = [DAY - timedelta(days=back) for back in range(70)]

    keep = dumps_to_keep(dates)

    daily = {DAY - timedelta(days=back) for back in range(14)}
    # The newest dump of each of the 8 newest weeks: today, then 7 Sundays
    weekly = {DAY} | {date(2026, 11, 8) - timedelta(weeks=n) for n in range(7)}
    assert keep == daily | weekly
    assert len(keep) == 19


def test_weekly_dumps_skip_weeks_without_a_dump() -> None:
    dates = [date(2026, 9, 2), date(2026, 8, 5), date(2026, 8, 4)]
    assert dumps_to_keep(dates, daily=0, weekly=2) == {date(2026, 9, 2), date(2026, 8, 5)}


def test_backup_writes_a_dump_and_prunes_old_ones(tmp_path: Path) -> None:
    for back in range(1, 20):
        write_dump(tmp_path, DAY - timedelta(days=back))
    write_dump(tmp_path, DAY, database="metabase_app")

    result = backup_database("warehouse", DAY, tmp_path, dump=fake_dump(120))

    target = tmp_path / "warehouse" / "warehouse-2026-11-09.dump"
    assert result.path == str(target)
    assert (result.size, result.previous_size) == (120, 100)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    # The 14 newest days from 2026-10-27, plus Sunday 2026-10-25 for its week
    assert result.pruned == [
        "warehouse-2026-10-21.dump",
        "warehouse-2026-10-22.dump",
        "warehouse-2026-10-23.dump",
        "warehouse-2026-10-24.dump",
        "warehouse-2026-10-26.dump",
    ]
    assert len(names(tmp_path / "warehouse")) == 15
    # Other databases are left alone
    assert names(tmp_path / "metabase_app") == ["metabase_app-2026-11-09.dump"]


def test_backup_again_for_the_same_day_replaces_the_dump(tmp_path: Path) -> None:
    write_dump(tmp_path, DAY - timedelta(days=1), size=100)
    write_dump(tmp_path, DAY, size=10)

    result = backup_database("warehouse", DAY, tmp_path, dump=fake_dump(90))

    # Compared with the day before, not with the dump it replaced
    assert result.previous_size == 100
    assert (tmp_path / "warehouse" / "warehouse-2026-11-09.dump").read_bytes() == b"x" * 90


def test_the_first_backup_has_nothing_to_compare(tmp_path: Path) -> None:
    result = backup_database("warehouse", DAY, tmp_path / "new", dump=fake_dump(5))
    assert result.previous_size is None
    assert result.pruned == []


def test_a_dump_under_half_the_previous_fails_and_prunes_nothing(tmp_path: Path) -> None:
    for back in range(1, 20):
        write_dump(tmp_path, DAY - timedelta(days=back), size=100)

    with pytest.raises(BackupError, match=r"under half of warehouse-2026-11-08\.dump"):
        backup_database("warehouse", DAY, tmp_path, dump=fake_dump(49))

    # The small dump is kept to look at, and every older one is still there
    assert len(names(tmp_path / "warehouse")) == 20


def test_a_failed_dump_leaves_no_file(tmp_path: Path) -> None:
    write_dump(tmp_path, DAY - timedelta(days=1))

    def broken(database: str, path: Path) -> None:
        path.write_bytes(b"half")
        raise BackupError("pg_dump exited with 1")

    with pytest.raises(BackupError, match="exited with 1"):
        backup_database("warehouse", DAY, tmp_path, dump=broken)
    assert names(tmp_path / "warehouse") == ["warehouse-2026-11-08.dump"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_a_folder_that_is_not_writable_fails(tmp_path: Path) -> None:
    (tmp_path / "warehouse").mkdir(mode=0o500)
    with pytest.raises(BackupError, match="not writable"):
        backup_database("warehouse", DAY, tmp_path, dump=fake_dump(1))


def test_a_folder_that_cannot_be_created_fails(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    with pytest.raises(BackupError, match="cannot create"):
        backup_database("warehouse", DAY, tmp_path / "file", dump=fake_dump(1))


def test_pg_dump_command_reads_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.delenv("BACKUP_USER", raising=False)
    command = pg_dump_command("warehouse", Path("/backups/w.dump.partial"))
    assert command == [
        "pg_dump",
        "--format=custom",
        "--no-password",
        "--host",
        "postgres",
        "--port",
        "5433",
        "--username",
        "backup",
        "--dbname",
        "warehouse",
        "--file",
        "/backups/w.dump.partial",
    ]


def test_pg_dump_needs_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BACKUP_PASSWORD", raising=False)
    with pytest.raises(BackupError, match="BACKUP_PASSWORD"):
        pg_dump("warehouse", Path("w.dump"))


class FakeRun:
    def __init__(self, returncodes: list[int]) -> None:
        self.returncodes = returncodes
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []

    def __call__(self, command: list[str], env: Any = None, **kwargs: Any) -> Any:
        self.calls.append((command, env))
        code = self.returncodes[len(self.calls) - 1]
        return subprocess.CompletedProcess(command, code, "", "  boom\n")


def test_pg_dump_dumps_then_reads_the_dump_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_PASSWORD", "secret")
    run = FakeRun([0, 0])
    monkeypatch.setattr(subprocess, "run", run)

    pg_dump("warehouse", Path("w.dump"))

    (dump_command, dump_env), (list_command, _) = run.calls
    assert dump_command[0] == "pg_dump"
    # The password goes in the environment, never on the command line
    assert dump_env is not None and dump_env["PGPASSWORD"] == "secret"
    assert "secret" not in dump_command
    assert list_command == ["pg_restore", "--list", "w.dump"]


def test_pg_dump_failure_carries_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_PASSWORD", "secret")
    run = FakeRun([1])
    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(BackupError, match="pg_dump exited with 1: boom"):
        pg_dump("warehouse", Path("w.dump"))
    assert len(run.calls) == 1
