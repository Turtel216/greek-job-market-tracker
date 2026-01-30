"""Task failure and retry alerts, used as on_failure_callback and
on_retry_callback by every DAG.

Alerts go to the Discord webhook in ALERT_DISCORD_WEBHOOK_URL. When it is
not set or does not answer, they go by email to ALERT_EMAIL through the
smtp_default connection, defined from the environment as
AIRFLOW_CONN_SMTP_DEFAULT. An alert that cannot be sent is only logged, so
it never changes the outcome of a task.
"""

import html
import logging
import os
from typing import Any

import requests

from common.rundate import run_date_from_context

log = logging.getLogger(__name__)

# Discord rejects longer messages
DISCORD_MAX_CHARS = 2000
TIMEOUT_SECONDS = 10


def task_failed(context: Any) -> None:
    send(failure_message(context))


def task_retried(context: Any) -> None:
    send(retry_message(context))


def failure_message(context: Any) -> str:
    ti = context["ti"]
    header = "\n".join(
        [
            f"Task failed: {ti.dag_id}.{ti.task_id}",
            f"run_date: {run_date_from_context(context)}",
            f"attempt: {ti.try_number}",
            f"log: {ti.log_url}",
        ]
    )
    error = context.get("exception")
    if error is None:
        return header
    # The error carries the last log lines when the task ran a CLI command
    return with_details(header, f"{type(error).__name__}: {error}")


def retry_message(context: Any) -> str:
    ti = context["ti"]
    error = context.get("exception")
    lines = [
        f"Task retried: {ti.dag_id}.{ti.task_id}",
        f"run_date: {run_date_from_context(context)}",
        f"attempt: {ti.try_number} of {ti.max_tries + 1}",
    ]
    if error is not None:
        lines.append(f"error: {type(error).__name__}")
    return "\n".join(lines)


def with_details(header: str, details: str, limit: int = DISCORD_MAX_CHARS) -> str:
    """Add details in a code block, cutting from the front to fit the limit.

    The last lines of a log say the most about a failure, so they are kept.
    """
    # Stop the details from closing the code block early
    details = details.replace("```", "'''")
    start, end = f"{header}\n```\n", "\n```"
    room = limit - len(start) - len(end)
    if len(details) > room:
        details = "..." + details[-(room - 3) :]
    return start + details + end


def send(message: str) -> None:
    if send_discord(message):
        return
    send_email(message)


def send_discord(message: str) -> bool:
    url = os.environ.get("ALERT_DISCORD_WEBHOOK_URL")
    if not url:
        return False
    try:
        response = requests.post(
            url,
            # No @everyone or role pings from text inside an error
            json={"content": message, "allowed_mentions": {"parse": []}},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as err:
        # The webhook URL holds a token, so log the error class and status only
        status = err.response.status_code if err.response is not None else None
        log.warning("Discord alert failed: %s, status %s", type(err).__name__, status)
        return False
    return True


def send_email(message: str) -> bool:
    to = os.environ.get("ALERT_EMAIL")
    if not to:
        log.error("No alert channel answered, alert not sent:\n%s", message)
        return False
    try:
        # Imported here so a DAG file does not pay for it at parse time
        from airflow.providers.smtp.hooks.smtp import SmtpHook

        with SmtpHook() as hook:
            hook.send_email_smtp(
                to=to,
                subject=f"[jobtracker] {message.splitlines()[0]}",
                html_content=f"<pre>{html.escape(message)}</pre>",
            )
    except Exception:
        log.exception("Email alert failed, alert not sent:\n%s", message)
        return False
    return True
