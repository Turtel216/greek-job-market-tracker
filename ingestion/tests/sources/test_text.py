import pytest

from jobtracker_ingest.sources.text import markup_to_text, tidy_lines


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
