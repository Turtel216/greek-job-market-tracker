import pytest

from jobtracker_ingest.sources.jsliteral import read_value, unescape


def value(text: str) -> object:
    return read_value(text)[0]


def test_reads_an_object_with_bare_and_quoted_keys() -> None:
    text = '{id:7,"@type":"Place",0:"zero",$key:null}'
    assert value(text) == {"id": 7, "@type": "Place", "0": "zero", "$key": None}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("!0", True),
        ("!1", False),
        ("null", None),
        ("void 0", None),
        ("undefined", None),
        ("true", True),
        ("false", False),
        ("12", 12),
        ("-3", -3),
        ("1250.5", 1250.5),
        (".5", 0.5),
        ("1e3", 1000.0),
        ("1794043781455", 1794043781455),
    ],
)
def test_reads_words_and_numbers(text: str, expected: object) -> None:
    assert value(text) == expected


def test_returns_where_the_value_ends() -> None:
    text = '$R[0]={a:[1,2]})($R["tsr"])'
    assert read_value(text) == ({"a": [1, 2]}, len("$R[0]={a:[1,2]}"))
    assert read_value("x={b:1}", 2) == ({"b": 1}, 7)


def test_reads_arrays_with_holes_and_space() -> None:
    assert value("[ 1 , [] , {} ]") == [1, [], {}]
    assert value("[,2,]") == [None, 2, None]
    assert value("[]") == []


def test_a_slot_can_be_referred_to_later() -> None:
    text = '{a:$R[3]={city:"Πάτρα"},b:$R[3],c:$R[9]}'
    # $R[9] was filled outside the part being read, so it is unknown
    assert value(text) == {"a": {"city": "Πάτρα"}, "b": {"city": "Πάτρα"}, "c": None}


def test_a_comparison_is_not_a_slot() -> None:
    with pytest.raises(ValueError, match="expected ','"):
        value('{a:$R[1]=="x"}')


@pytest.mark.parametrize(
    ("body", "text"),
    [
        (r"\x3Cbr>", "<br>"),
        (r"caf\u00e9", "caf\u00e9"),
        (r"\u{1F43E}", "\U0001f43e"),
        (r"\uD83D\uDC3E", "\U0001f43e"),
        (r"a\nb\tc", "a\nb\tc"),
        (r"\"quoted\" \\ \'", '"quoted" \\ \''),
        ("one \\\ntwo", "one two"),
        (r"\0\b\f\v\r", "\0\b\f\v\r"),
        (r"\q", "q"),
        ("plain 🐾", "plain 🐾"),
    ],
)
def test_unescape(body: str, text: str) -> None:
    assert unescape(body) == text


def test_reads_both_kinds_of_string() -> None:
    assert value(r'"say \"hi\""') == 'say "hi"'
    assert value(r"'it\'s'") == "it's"


def test_a_word_must_end_where_it_should() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        value("nullable")
    with pytest.raises(ValueError, match="unsupported"):
        value("10n")


@pytest.mark.parametrize(
    "text",
    [
        "function(){}",
        "new Map([[1,2]])",
        "Object.assign(Object.create(null),{a:1})",
        "",
    ],
)
def test_anything_else_is_an_error(text: str) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        value(text)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('"no end', "unterminated string"),
        ("{a 1}", "expected ':'"),
        ("{a:1 b:2}", "expected ','"),
        ("[1 2]", "expected ','"),
        ("{-:1}", "expected a key"),
        ("{a:1", "expected ','"),
    ],
)
def test_broken_literals_are_errors(text: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        value(text)
