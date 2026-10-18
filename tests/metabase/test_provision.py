"""Tests for metabase/provision.py and the dashboard definitions.

Run with `make test-metabase`. They need no Metabase; `make test-dbt` runs
every question against the built marts.
"""

import re
import sys
from pathlib import Path
from typing import Any

import pytest

METABASE = Path(__file__).resolve().parents[2] / "metabase"
sys.path.insert(0, str(METABASE))

import provision  # noqa: E402

DASHBOARDS = provision.load_dashboards()
# The schemas each dashboard may read. The public one reads marts only.
ALLOWED_SCHEMAS = {
    "Greek tech job market": {"marts"},
    "Pipeline health": {"qa", "ops"},
}
SCHEMA = re.compile(r"\b(raw|ops|staging|snapshots|intermediate|marts|qa)\.\w+")

PARAMETERS = [
    {"slug": "role_family", "name": "Role family", "values": ["data", "backend"]},
    {
        "slug": "salary_basis",
        "name": "Salary basis",
        "values": ["gross", "net"],
        "default": "gross",
    },
]
SQL = """select count(*) from marts.dim_jobs
where true
    [[and role_family = {{role_family}}]]
    [[and salary_basis = {{ salary_basis }}]]
"""


def questions(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    return [card for card in dashboard["cards"] if "query" in card]


def test_both_dashboards_are_defined() -> None:
    assert sorted(d["name"] for d in DASHBOARDS) == sorted(ALLOWED_SCHEMAS)
    public = [d["name"] for d in DASHBOARDS if d.get("public")]
    # Only the public dashboard gets a public link
    assert public == ["Greek tech job market"]


def test_every_sql_file_is_used_once() -> None:
    used = [card["sql"] for d in DASHBOARDS for card in questions(d)]
    files = sorted(
        str(path.relative_to(provision.QUESTIONS)) for path in provision.QUESTIONS.rglob("*.sql")
    )
    assert sorted(used) == files


@pytest.mark.parametrize("dashboard", DASHBOARDS, ids=lambda d: d["name"])
def test_titles_are_unique(dashboard: dict[str, Any]) -> None:
    titles = [card["title"] for card in questions(dashboard)]
    assert len(titles) == len(set(titles))


@pytest.mark.parametrize("dashboard", DASHBOARDS, ids=lambda d: d["name"])
def test_cards_fit_the_grid_without_overlapping(dashboard: dict[str, Any]) -> None:
    taken: dict[tuple[int, int], str] = {}
    for card in dashboard["cards"]:
        name = card.get("title") or card.get("heading") or "text"
        assert card["col"] >= 0 and card["col"] + card["width"] <= provision.GRID_WIDTH, name
        for row in range(card["row"], card["row"] + card["height"]):
            for col in range(card["col"], card["col"] + card["width"]):
                assert (row, col) not in taken, f"{name} overlaps {taken[(row, col)]}"
                taken[(row, col)] = name


@pytest.mark.parametrize("dashboard", DASHBOARDS, ids=lambda d: d["name"])
def test_questions_read_only_their_schemas(dashboard: dict[str, Any]) -> None:
    for card in questions(dashboard):
        schemas = set(SCHEMA.findall(card["query"]))
        assert schemas, card["sql"]
        assert schemas <= ALLOWED_SCHEMAS[dashboard["name"]], card["sql"]


@pytest.mark.parametrize("dashboard", DASHBOARDS, ids=lambda d: d["name"])
def test_every_filter_tag_has_a_parameter(dashboard: dict[str, Any]) -> None:
    parameters = dashboard.get("parameters", [])
    for card in questions(dashboard):
        # Raises on a tag with no parameter
        provision.template_tags(card["query"], parameters)
    for parameter in parameters:
        assert parameter.get("default", parameter["values"][0]) in parameter["values"]


def test_every_public_filter_is_used() -> None:
    [public] = [d for d in DASHBOARDS if d.get("public")]
    used = {name for card in questions(public) for name in provision.tag_names(card["query"])}
    assert used == {p["slug"] for p in public["parameters"]}


def test_template_tags_are_text_with_stable_ids() -> None:
    tags = provision.template_tags(SQL, PARAMETERS)
    assert list(tags) == ["role_family", "salary_basis"]
    assert tags["role_family"]["type"] == "text"
    assert tags["role_family"]["display-name"] == "Role family"
    assert tags == provision.template_tags(SQL, PARAMETERS)
    assert tags["role_family"]["id"] != tags["salary_basis"]["id"]


def test_a_tag_without_a_parameter_is_refused() -> None:
    with pytest.raises(ValueError, match="city"):
        provision.template_tags("select 1 [[where city = {{city}}]]", PARAMETERS)


def test_dashboard_parameters_are_static_dropdowns() -> None:
    role, basis = provision.dashboard_parameters({"parameters": PARAMETERS})
    assert role["id"] == role["slug"] == "role_family"
    assert role["values_source_type"] == "static-list"
    assert role["values_source_config"] == {"values": ["data", "backend"]}
    assert "default" not in role
    assert basis["default"] == ["gross"]


def test_card_payload_is_a_native_question() -> None:
    card = {"title": "Jobs", "display": "scalar", "query": SQL}
    payload = provision.card_payload(card, database_id=2, collection_id=5, parameters=PARAMETERS)
    assert payload["name"] == "Jobs"
    assert payload["collection_id"] == 5
    assert payload["visualization_settings"] == {}
    query = payload["dataset_query"]
    assert (query["type"], query["database"]) == ("native", 2)
    assert query["native"]["query"] == SQL
    assert set(query["native"]["template-tags"]) == {"role_family", "salary_basis"}


def test_dashcards_place_cards_and_wire_filters() -> None:
    dashboard = {
        "cards": [
            {"heading": "Skills", "row": 0, "col": 0, "width": 24, "height": 1},
            {"text": "About", "row": 1, "col": 0, "width": 24, "height": 2},
            {"title": "Jobs", "query": SQL, "row": 3, "col": 0, "width": 6, "height": 3},
            {"title": "All", "query": "select 1", "row": 3, "col": 6, "width": 6, "height": 3},
        ]
    }
    heading, text, jobs, plain = provision.dashcards(dashboard, {"Jobs": 7, "All": 8}, PARAMETERS)

    assert heading["card_id"] is None
    assert heading["visualization_settings"]["virtual_card"]["display"] == "heading"
    assert heading["visualization_settings"]["text"] == "Skills"
    assert text["visualization_settings"]["virtual_card"]["display"] == "text"

    assert (jobs["card_id"], jobs["row"], jobs["col"], jobs["size_x"], jobs["size_y"]) == (
        7,
        3,
        0,
        6,
        3,
    )
    assert jobs["parameter_mappings"] == [
        {
            "parameter_id": "role_family",
            "card_id": 7,
            "target": ["variable", ["template-tag", "role_family"]],
        },
        {
            "parameter_id": "salary_basis",
            "card_id": 7,
            "target": ["variable", ["template-tag", "salary_basis"]],
        },
    ]
    assert plain["parameter_mappings"] == []
    # New dashboard cards get distinct negative ids
    assert sorted(card["id"] for card in (heading, text, jobs, plain)) == [-4, -3, -2, -1]


def test_fill_sql_keeps_only_clauses_with_a_value() -> None:
    filled = provision.fill_sql(SQL, {"role_family": "data"})
    assert "and role_family = 'data'" in filled
    assert "salary_basis" not in filled
    assert "[[" not in filled
    assert "{{" not in provision.fill_sql(SQL, {})


def test_fill_sql_quotes_values() -> None:
    filled = provision.fill_sql(SQL, {"role_family": "o'brien"})
    assert "'o''brien'" in filled


def test_check_statements_run_every_question() -> None:
    statements = provision.check_statements(DASHBOARDS)
    selects = [s for s in statements if s.startswith("select")]
    with_tags = [c for d in DASHBOARDS for c in questions(d) if provision.tag_names(c["query"])]
    total = sum(len(questions(d)) for d in DASHBOARDS)
    # Once with no filters, and once more with filters for those that have them
    assert len(selects) == total + len(with_tags)
    assert not any("{{" in s or "[[" in s for s in selects)
