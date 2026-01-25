import sys

import pytest

from common.cli import TAIL_LINES, CliOperator, CommandFailed, SourceBlocked


def run(script: str) -> None:
    op = CliOperator(task_id="cli", command=[sys.executable, "-c", script])
    op.execute(context={})


def test_success_returns_quietly() -> None:
    run("print('ok')")


def test_failure_carries_the_last_lines() -> None:
    script = "import sys; [print(f'line {i}') for i in range(50)]; sys.exit(1)"
    with pytest.raises(CommandFailed) as err:
        run(script)

    lines = str(err.value).splitlines()
    assert "exited with 1" in lines[0]
    assert lines[1:] == [f"line {i}" for i in range(50 - TAIL_LINES, 50)]


def test_stderr_is_kept_too() -> None:
    with pytest.raises(CommandFailed, match="went wrong"):
        run("import sys; print('went wrong', file=sys.stderr); sys.exit(1)")


def test_a_block_is_not_retried() -> None:
    with pytest.raises(SourceBlocked, match="exited with 3"):
        run("import sys; print('403 Forbidden'); sys.exit(3)")


def test_command_is_templated() -> None:
    assert "command" in CliOperator.template_fields
