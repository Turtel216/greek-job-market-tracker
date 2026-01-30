from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from common import alerts
from common.cli import CommandFailed

WEBHOOK = "https://discord.example/api/webhooks/1/secret-token"


def make_context(error: BaseException | None = None) -> dict[str, Any]:
    ti = SimpleNamespace(
        dag_id="jobtracker_daily",
        task_id="greenhouse.extract",
        try_number=2,
        max_tries=2,
        log_url="http://localhost:8080/dags/jobtracker_daily/runs/r1/tasks/greenhouse.extract",
    )
    # 06:00 in Athens on 2026-10-24
    dag_run = SimpleNamespace(run_after=datetime(2026, 10, 24, 3, 0, tzinfo=UTC))
    return {"ti": ti, "dag_run": dag_run, "exception": error}


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for url {WEBHOOK}", response=self)


@pytest.fixture
def posts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def post(url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        sent.append({"url": url, **json})
        return FakeResponse(204)

    monkeypatch.setattr(alerts.requests, "post", post)
    monkeypatch.setenv("ALERT_DISCORD_WEBHOOK_URL", WEBHOOK)
    return sent


@pytest.fixture
def emails(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    def send_email(message: str) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(alerts, "send_email", send_email)
    return sent


def test_failure_message_has_what_the_design_asks_for() -> None:
    error = CommandFailed("jobtracker extract exited with 1\nline a\nline b")
    message = alerts.failure_message(make_context(error))

    assert message.startswith("Task failed: jobtracker_daily.greenhouse.extract\n")
    assert "run_date: 2026-10-24" in message
    assert "attempt: 2" in message
    assert "log: http://localhost:8080/dags/jobtracker_daily/" in message
    assert message.endswith("CommandFailed: jobtracker extract exited with 1\nline a\nline b\n```")


def test_failure_message_without_an_exception() -> None:
    message = alerts.failure_message(make_context())
    assert "```" not in message
    assert "run_date: 2026-10-24" in message


def test_retry_message_has_attempt_and_error_class() -> None:
    message = alerts.retry_message(make_context(CommandFailed("boom\nmore")))
    assert message.splitlines() == [
        "Task retried: jobtracker_daily.greenhouse.extract",
        "run_date: 2026-10-24",
        "attempt: 2 of 3",
        "error: CommandFailed",
    ]


def test_long_details_keep_the_last_lines() -> None:
    details = "\n".join(f"line {i}" for i in range(1000))
    message = alerts.with_details("header", details)

    assert len(message) == alerts.DISCORD_MAX_CHARS
    assert message.startswith("header\n```\n...")
    assert message.endswith("line 999\n```")


def test_details_cannot_close_the_code_block() -> None:
    message = alerts.with_details("header", "a ``` b")
    assert message.count("```") == 2


def test_alerts_go_to_discord(posts: list[dict[str, Any]], emails: list[str]) -> None:
    alerts.task_failed(make_context(CommandFailed("boom")))

    assert len(posts) == 1
    assert posts[0]["url"] == WEBHOOK
    assert posts[0]["content"].startswith("Task failed:")
    assert posts[0]["allowed_mentions"] == {"parse": []}
    assert emails == []


def test_email_is_the_fallback_when_discord_fails(
    monkeypatch: pytest.MonkeyPatch, emails: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALERT_DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setattr(alerts.requests, "post", lambda *a, **k: FakeResponse(500))

    alerts.task_retried(make_context(CommandFailed("boom")))

    assert len(emails) == 1
    assert emails[0].startswith("Task retried:")
    # The webhook token never reaches the logs
    assert "secret-token" not in caplog.text


def test_email_is_the_fallback_when_discord_is_down(
    monkeypatch: pytest.MonkeyPatch, emails: list[str]
) -> None:
    def down(*args: Any, **kwargs: Any) -> None:
        raise requests.ConnectionError("no route")

    monkeypatch.setenv("ALERT_DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setattr(alerts.requests, "post", down)

    alerts.task_failed(make_context())
    assert len(emails) == 1


def test_email_is_used_without_a_webhook(
    monkeypatch: pytest.MonkeyPatch, emails: list[str]
) -> None:
    monkeypatch.delenv("ALERT_DISCORD_WEBHOOK_URL", raising=False)
    alerts.task_failed(make_context())
    assert len(emails) == 1


def test_send_email_uses_the_smtp_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from airflow.providers.smtp.hooks import smtp

    sent: list[dict[str, Any]] = []

    class FakeHook:
        def __enter__(self) -> "FakeHook":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def send_email_smtp(self, **kwargs: Any) -> None:
            sent.append(kwargs)

    monkeypatch.setattr(smtp, "SmtpHook", FakeHook)
    monkeypatch.setenv("ALERT_EMAIL", "alerts@example.org")

    assert alerts.send_email("Task failed: x\n<b>details</b>")
    assert sent[0]["to"] == "alerts@example.org"
    assert sent[0]["subject"] == "[jobtracker] Task failed: x"
    assert "&lt;b&gt;details&lt;/b&gt;" in sent[0]["html_content"]


def test_send_email_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_EMAIL", "alerts@example.org")
    # No smtp_default connection is defined in the tests
    monkeypatch.delenv("AIRFLOW_CONN_SMTP_DEFAULT", raising=False)
    assert alerts.send_email("Task failed: x") is False


def test_no_channel_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALERT_DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    alerts.task_failed(make_context())
