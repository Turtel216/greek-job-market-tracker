import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from common import summary
from common.summary import DbtReport, SourceStatus, dbt_report, node_name, summary_message

NO_PROBLEMS = DbtReport(problems=[], missing_steps=[])


def write_results(folder: Path, step: str, results: list[dict[str, Any]]) -> None:
    path = folder / step / summary.STEPS[step]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"results": results}))


@pytest.mark.parametrize(
    ("unique_id", "name"),
    [
        ("test.jobtracker.not_null_dim_jobs_job_id.5f3a1b2c4d", "not_null_dim_jobs_job_id"),
        ("source.jobtracker.raw.raw_postings", "raw.raw_postings"),
        ("model.jobtracker.dim_jobs", "dim_jobs"),
    ],
)
def test_node_name(unique_id: str, name: str) -> None:
    assert node_name(unique_id) == name


def test_dbt_report_lists_warnings_failures_and_missing_steps(tmp_path: Path) -> None:
    write_results(
        tmp_path,
        "freshness",
        [{"unique_id": "source.jobtracker.raw.raw_postings", "status": "warn"}],
    )
    write_results(
        tmp_path,
        "staging",
        [
            {"unique_id": "model.jobtracker.stg_postings", "status": "success"},
            {"unique_id": "test.jobtracker.unique_stg_postings_posting_key.1a", "status": "pass"},
            {"unique_id": "test.jobtracker.volume_vs_7d_avg_stg_raw_postings.2b", "status": "fail"},
            {"unique_id": "model.jobtracker.stg_raw_postings", "status": "skipped"},
        ],
    )

    report = dbt_report(tmp_path)

    assert report.problems == [
        "warn: raw.raw_postings",
        "fail: volume_vs_7d_avg_stg_raw_postings",
    ]
    # The later steps never ran
    assert report.missing_steps == ["snapshots", "intermediate", "marts"]


def test_dbt_report_with_no_artifacts(tmp_path: Path) -> None:
    assert dbt_report(tmp_path / "2026-10-24") == DbtReport([], list(summary.STEPS))


def test_message_for_a_good_day() -> None:
    status = {
        "greenhouse": SourceStatus(date(2026, 10, 24), 12, 240, 3),
        "kariera": SourceStatus(date(2026, 10, 24), 30, 410, 8),
    }
    message = summary_message("2026-10-24", ["greenhouse", "kariera"], status, NO_PROBLEMS)
    assert message == (
        "Daily summary for 2026-10-24\n"
        "greenhouse: 12 new, 3 closed, 240 open\n"
        "kariera: 30 new, 8 closed, 410 open\n"
        "dbt: every step ran with no warnings"
    )


def test_message_for_a_bad_day() -> None:
    status = {"greenhouse": SourceStatus(date(2026, 10, 23), 5, 230, 1)}
    report = DbtReport(["error: volume_vs_7d_avg_stg_raw_postings"], ["snapshots", "marts"])

    message = summary_message("2026-10-24", ["greenhouse", "kariera"], status, report)

    assert message.splitlines() == [
        "Daily summary for 2026-10-24",
        "greenhouse: no successful run today, last on 2026-10-23",
        "kariera: no successful run yet",
        "dbt steps with no results: snapshots, marts. "
        "The counts above may be from an earlier build.",
        "dbt warnings and failures:",
        "- error: volume_vs_7d_avg_stg_raw_postings",
    ]


def test_message_cuts_a_long_list() -> None:
    report = DbtReport([f"warn: test_{n}" for n in range(25)], [])
    lines = summary_message("2026-10-24", [], {}, report).splitlines()
    assert lines[-2] == "- warn: test_19"
    assert lines[-1] == "- and 5 more"


def test_notify_summary_sends_the_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(summary, "ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(summary, "enabled_sources", lambda: ["greenhouse"])
    monkeypatch.setattr(
        summary,
        "source_status",
        lambda: {"greenhouse": SourceStatus(date(2026, 10, 24), 1, 2, 0)},
    )
    monkeypatch.setattr(summary.alerts, "send", sent.append)

    summary.notify_summary("2026-10-24")

    assert len(sent) == 1
    assert "greenhouse: 1 new, 0 closed, 2 open" in sent[0]
    assert "dbt steps with no results: freshness, staging" in sent[0]
