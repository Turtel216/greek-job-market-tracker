from datetime import UTC, datetime

import pytest

from common.rundate import athens_date


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        # 06:00 in Athens during summer time
        (datetime(2026, 10, 24, 3, 0, tzinfo=UTC), "2026-10-24"),
        # Still the 24th in UTC, already the 25th in Athens
        (datetime(2026, 10, 24, 22, 30, tzinfo=UTC), "2026-10-25"),
        # Summer time ends on 2026-10-25, Athens is UTC+2 after that
        (datetime(2026, 10, 26, 4, 0, tzinfo=UTC), "2026-10-26"),
        (datetime(2026, 12, 31, 22, 0, tzinfo=UTC), "2027-01-01"),
    ],
)
def test_athens_date(when: datetime, expected: str) -> None:
    assert athens_date(when) == expected
