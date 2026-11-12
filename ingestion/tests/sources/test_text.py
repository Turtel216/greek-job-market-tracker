import re

import pytest

from jobtracker_ingest.sources.text import (
    as_list,
    job_id_from_url,
    joined,
    json_hash,
    markup_to_text,
    tidy_lines,
)


@pytest.mark.parametrize(
    ("text", "tidy"),
    [
        ("The Role\n\n  Build   APIs \r\n\twith Python  ", "The Role\nBuild APIs\nwith Python"),
        ("one line", "one line"),
        (" \n \t\n", None),
        ("", None),
    ],
)
def test_tidy_lines(text: str, tidy: str | None) -> None:
    assert tidy_lines(text) == tidy


def test_markup_to_text_puts_blocks_on_their_own_lines() -> None:
    markup = "<p>Hello <b>there</b></p><ul><li>Python</li><li>SQL<br>dbt</li></ul>"
    assert markup_to_text(markup) == "Hello there\nPython\nSQL\ndbt"


@pytest.mark.parametrize(
    ("url", "job_id"),
    [
        ("https://example.gr/jobs/it-jobs/358246", "358246"),
        ("https://example.gr/jobs/it-jobs/358246/", "358246"),
        ("https://example.gr/jobs/it-jobs", None),
        (None, None),
    ],
)
def test_job_id_from_url(url: str | None, job_id: str | None) -> None:
    assert job_id_from_url(url, re.compile(r"/(\d+)/?$")) == job_id


@pytest.mark.parametrize(
    ("value", "items"),
    [(None, []), ("a", ["a"]), (["a", "b"], ["a", "b"]), ({"a": 1}, [{"a": 1}])],
)
def test_as_list(value: object, items: list[object]) -> None:
    assert as_list(value) == items


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (" Full-time ", "Full-time"),
        (["Engineering", " ", None, "Data "], "Engineering, Data"),
        ([3, {"name": "x"}], None),
        ([], None),
        (None, None),
    ],
)
def test_joined(value: object, text: str | None) -> None:
    assert joined(value) == text


def test_json_hash_is_stable() -> None:
    # Stored fingerprints were made with exactly this encoding: sorted keys,
    # non-ASCII text kept as is
    expected = "38573c8cbfea4ef51d44f3696ddb9588c217516d060a75817ceb06b9e9c89996"
    assert json_hash({"b": [1, None], "a": "Αθήνα"}) == expected
