"""Sets up Metabase from the definitions in this folder.

Creates the first admin user, the warehouse connection as bi_reader, and
one collection and dashboard per file in dashboards/. Each question's SQL
is a file in questions/. Running it again brings Metabase back in line with
the files: changed questions are updated, removed ones are archived, and
the dashboard layout is replaced. Edits made in the Metabase UI to these
dashboards are lost on the next run, so make them here instead.

The metabase-init service runs it on `make up`, and `make dashboards` runs
it again. It uses only the standard library, so it runs in a plain Python
image.

    python metabase/provision.py           # provision a running Metabase
    python metabase/provision.py check-sql # print every question as plain SQL

check-sql fills in the filters and prints one statement per question and
filter set, which `make test-dbt` runs as bi_reader against the built marts.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DASHBOARDS = ROOT / "dashboards"
QUESTIONS = ROOT / "questions"

DATABASE_NAME = "Warehouse"
# Metabase sees only the schemas the dashboards may read
DATABASE_SCHEMAS = "marts,qa,ops"
# Width of the Metabase dashboard grid
GRID_WIDTH = 24
# Stable ids, so a template tag keeps its id across runs
TAG_NAMESPACE = uuid.UUID("5b1c6f0e-4d2a-4e8b-9c3f-7a6d1e2b0c94")

TAG = re.compile(r"\{\{\s*(\w+)\s*\}\}")
OPTIONAL = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)


class MetabaseError(Exception):
    pass


# Definitions


def load_dashboards(folder: Path = DASHBOARDS) -> list[dict[str, Any]]:
    """Every dashboard definition, with each card's SQL read in."""
    dashboards = []
    for path in sorted(folder.glob("*.json")):
        dashboard = json.loads(path.read_text())
        for card in dashboard["cards"]:
            if "sql" in card:
                card["query"] = (QUESTIONS / card["sql"]).read_text()
        dashboards.append(dashboard)
    return dashboards


def tag_names(sql: str) -> list[str]:
    """The template tags in a question, in order of first use."""
    return list(dict.fromkeys(TAG.findall(sql)))


def template_tags(sql: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
    """A text template tag for every filter the question uses.

    Each tag must match a dashboard parameter by slug, which gives it its
    display name.
    """
    by_slug = {p["slug"]: p for p in parameters}
    tags = {}
    for name in tag_names(sql):
        if name not in by_slug:
            raise ValueError(f"template tag {name} has no dashboard parameter")
        tags[name] = {
            "id": str(uuid.uuid5(TAG_NAMESPACE, name)),
            "name": name,
            "display-name": by_slug[name]["name"],
            "type": "text",
            "required": False,
        }
    return tags


def dashboard_parameters(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Dashboard filters, each a dropdown of the values listed in the file."""
    parameters = []
    for p in dashboard.get("parameters", []):
        parameter = {
            "id": p["slug"],
            "name": p["name"],
            "slug": p["slug"],
            "type": "string/=",
            "sectionId": "string",
            "values_query_type": "list",
            "values_source_type": "static-list",
            "values_source_config": {"values": p["values"]},
        }
        if "default" in p:
            parameter["default"] = [p["default"]]
        parameters.append(parameter)
    return parameters


def card_payload(
    card: dict[str, Any], database_id: int, collection_id: int, parameters: list[dict[str, Any]]
) -> dict[str, Any]:
    """The body Metabase takes to create or update a native question."""
    return {
        "name": card["title"],
        "description": card.get("description"),
        "collection_id": collection_id,
        "display": card["display"],
        "visualization_settings": card.get("settings", {}),
        "dataset_query": {
            "type": "native",
            "database": database_id,
            "native": {
                "query": card["query"],
                "template-tags": template_tags(card["query"], parameters),
            },
        },
    }


def text_settings(text: str, display: str) -> dict[str, Any]:
    """Settings of a dashboard text box or heading, which has no question behind it."""
    return {
        "text": text,
        "virtual_card": {
            "name": None,
            "display": display,
            "visualization_settings": {},
            "dataset_query": {},
            "archived": False,
        },
    }


def dashcards(
    dashboard: dict[str, Any], card_ids: dict[str, int], parameters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every card's place on the grid, with the filters wired to its tags.

    New dashboard cards get negative ids, which Metabase replaces.
    """
    slugs = {p["slug"] for p in parameters}
    result = []
    for index, card in enumerate(dashboard["cards"], start=1):
        place = {
            "id": -index,
            "row": card["row"],
            "col": card["col"],
            "size_x": card["width"],
            "size_y": card["height"],
        }
        if "query" not in card:
            display = "heading" if "heading" in card else "text"
            result.append(
                {
                    **place,
                    "card_id": None,
                    "parameter_mappings": [],
                    "visualization_settings": text_settings(card[display], display),
                }
            )
            continue
        card_id = card_ids[card["title"]]
        mappings = [
            {
                "parameter_id": name,
                "card_id": card_id,
                "target": ["variable", ["template-tag", name]],
            }
            for name in tag_names(card["query"])
            if name in slugs
        ]
        result.append(
            {
                **place,
                "card_id": card_id,
                "parameter_mappings": mappings,
                "visualization_settings": {},
            }
        )
    return result


def fill_sql(sql: str, values: dict[str, str]) -> str:
    """The SQL Metabase would run: optional clauses whose tags all have a
    value are kept with the value quoted in, the rest are dropped."""

    def optional(match: re.Match[str]) -> str:
        clause = match.group(1)
        if all(name in values for name in tag_names(clause)):
            return clause
        return ""

    def tag(match: re.Match[str]) -> str:
        value = values[match.group(1)].replace("'", "''")
        return f"'{value}'"

    return TAG.sub(tag, OPTIONAL.sub(optional, sql))


def check_statements(dashboards: list[dict[str, Any]]) -> list[str]:
    """psql input that runs every question with no filters and with every
    filter set, so a broken question fails before it reaches Metabase."""
    statements = []
    for dashboard in dashboards:
        filters = {
            p["slug"]: p.get("default", p["values"][0]) for p in dashboard.get("parameters", [])
        }
        for card in dashboard["cards"]:
            if "query" not in card:
                continue
            variants = [("no filters", {})]
            if tag_names(card["query"]):
                variants.append(("filters", filters))
            for label, values in variants:
                sql = fill_sql(card["query"], values).strip().rstrip(";")
                statements.append(f"\\echo {card['sql']} ({label})")
                statements.append(f"select count(*) from (\n{sql}\n) as question;")
    return statements


# Metabase API


class Metabase:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.session: str | None = None

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"{self.url}/api/{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.session:
            request.add_header("X-Metabase-Session", self.session)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode()
        except urllib.error.HTTPError as err:
            raise MetabaseError(f"{method} {path}: {err.code} {err.read().decode()[:500]}") from err
        return json.loads(text) if text else None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, body if body is not None else {})

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, body)


def wait_until_healthy(api: Metabase, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if api.get("health") == {"status": "ok"}:
                return
        except (MetabaseError, OSError):
            pass
        if time.monotonic() > deadline:
            raise MetabaseError("Metabase did not become healthy")
        time.sleep(5)


def log_in(api: Metabase, email: str, password: str) -> None:
    """Log in as the admin, creating it first on a new Metabase."""
    properties = api.get("session/properties")
    if not properties.get("has-user-setup"):
        api.post(
            "setup",
            {
                "token": properties["setup-token"],
                "user": {
                    "email": email,
                    "password": password,
                    "first_name": "Jobtracker",
                    "last_name": "Admin",
                },
                "prefs": {"site_name": "Greek Tech Job Market", "allow_tracking": False},
            },
        )
        print(f"created the admin {email}")
    api.session = api.post("session", {"username": email, "password": password})["id"]


def ensure_database(api: Metabase, env: dict[str, str]) -> int:
    """The warehouse connection, as bi_reader. Returns its id."""
    details = {
        "host": env["POSTGRES_HOST"],
        "port": int(env.get("POSTGRES_PORT", "5432")),
        "dbname": env["WAREHOUSE_DB"],
        "user": "bi_reader",
        "password": env["BI_READER_PASSWORD"],
        "ssl": False,
        "schema-filters-type": "inclusion",
        "schema-filters-patterns": DATABASE_SCHEMAS,
    }
    body = {"name": DATABASE_NAME, "engine": "postgres", "details": details}
    existing = [db for db in api.get("database")["data"] if db["name"] == DATABASE_NAME]
    if existing:
        database_id = existing[0]["id"]
        api.put(f"database/{database_id}", body)
    else:
        database_id = api.post("database", body)["id"]
        print(f"added the {DATABASE_NAME} database")
    api.post(f"database/{database_id}/sync_schema")
    return int(database_id)


def ensure_collection(api: Metabase, name: str, description: str | None) -> int:
    for collection in api.get("collection"):
        if (
            collection["name"] == name
            and not collection.get("archived")
            and collection.get("location") == "/"
        ):
            return int(collection["id"])
    created = api.post("collection", {"name": name, "description": description, "parent_id": None})
    print(f"created the collection {name}")
    return int(created["id"])


def collection_items(api: Metabase, collection_id: int, model: str) -> dict[str, int]:
    """Items of one kind in a collection, by name. Archived ones are left out."""
    items = api.get(f"collection/{collection_id}/items?models={model}")
    data = items["data"] if isinstance(items, dict) else items
    return {item["name"]: int(item["id"]) for item in data}


def provision_dashboard(api: Metabase, dashboard: dict[str, Any], database_id: int) -> None:
    collection_id = ensure_collection(api, dashboard["collection"], dashboard.get("description"))
    parameters = dashboard_parameters(dashboard)

    existing_cards = collection_items(api, collection_id, "card")
    card_ids = {}
    for card in dashboard["cards"]:
        if "query" not in card:
            continue
        payload = card_payload(card, database_id, collection_id, parameters)
        card_id = existing_cards.pop(card["title"], None)
        if card_id is None:
            card_id = int(api.post("card", payload)["id"])
        else:
            api.put(f"card/{card_id}", payload)
        card_ids[card["title"]] = card_id
    # Questions no longer in the file
    for title, card_id in existing_cards.items():
        api.put(f"card/{card_id}", {"archived": True})
        print(f"archived the question {title}")

    existing = collection_items(api, collection_id, "dashboard")
    dashboard_id = existing.get(dashboard["name"])
    if dashboard_id is None:
        dashboard_id = int(
            api.post("dashboard", {"name": dashboard["name"], "collection_id": collection_id})["id"]
        )
    api.put(
        f"dashboard/{dashboard_id}",
        {
            "name": dashboard["name"],
            "description": dashboard.get("description"),
            "collection_id": collection_id,
            "parameters": parameters,
            "dashcards": dashcards(dashboard, card_ids, parameters),
        },
    )

    # The address people use, not the one inside Compose
    site_url = api.get("session/properties").get("site-url") or api.url
    if dashboard.get("public"):
        public = api.post(f"dashboard/{dashboard_id}/public_link")
        print(f"{dashboard['name']}: {site_url}/public/dashboard/{public['uuid']}")
    else:
        # Only the public dashboard may have a public link
        if api.get(f"dashboard/{dashboard_id}").get("public_uuid"):
            api.request("DELETE", f"dashboard/{dashboard_id}/public_link")
            print(f"removed the public link of {dashboard['name']}")
        print(f"{dashboard['name']}: {site_url}/dashboard/{dashboard_id} (admins only)")


def provision(env: dict[str, str]) -> None:
    api = Metabase(env.get("METABASE_URL", "http://localhost:3000"))
    wait_until_healthy(api)
    log_in(api, env["METABASE_ADMIN_EMAIL"], env["METABASE_ADMIN_PASSWORD"])
    database_id = ensure_database(api, env)
    for dashboard in load_dashboards():
        provision_dashboard(api, dashboard, database_id)


def main(argv: list[str]) -> int:
    if argv == ["check-sql"]:
        print("\n".join(check_statements(load_dashboards())))
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        provision(dict(os.environ))
    except MetabaseError as err:
        print(f"provisioning failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
