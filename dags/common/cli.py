"""Run a CLI command, such as `jobtracker extract`, as an Airflow task.

Every line the command prints goes to the task log. When the command fails,
its last lines go into the exception so the failure alert can show them.

Exit code 3 means the source blocked us. The task then fails without
retrying, because the source is stopped for the day and we never try to
get around a block.
"""

import subprocess
from collections import deque
from collections.abc import Sequence
from typing import Any

from airflow.sdk import BaseOperator
from airflow.sdk.exceptions import AirflowFailException

EXIT_BLOCKED = 3
TAIL_LINES = 20


class CommandFailed(Exception):
    """The command exited with an error. Airflow may retry it."""


class SourceBlocked(AirflowFailException):
    """The source blocked us. Airflow does not retry it."""


def failure_message(command: Sequence[str], returncode: int, tail: Sequence[str]) -> str:
    lines = [f"{' '.join(command)} exited with {returncode}", *tail]
    return "\n".join(lines)


class CliOperator(BaseOperator):
    template_fields: Sequence[str] = ("command",)

    def __init__(self, *, command: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.command = command
        self._process: subprocess.Popen[str] | None = None

    def execute(self, context: Any) -> None:
        self.log.info("Running %s", " ".join(self.command))
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        with subprocess.Popen(
            self.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        ) as process:
            self._process = process
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip()
                self.log.info("%s", line)
                tail.append(line)
        self._process = None

        if process.returncode == 0:
            return
        message = failure_message(self.command, process.returncode, tail)
        if process.returncode == EXIT_BLOCKED:
            raise SourceBlocked(message)
        raise CommandFailed(message)

    def on_kill(self) -> None:
        # Called on a timeout or when the task is marked failed by hand
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
