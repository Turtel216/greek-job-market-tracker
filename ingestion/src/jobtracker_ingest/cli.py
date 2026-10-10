"""Command line entry point, used by the Airflow tasks.

    jobtracker extract --source greenhouse --run-date 2026-10-25
    jobtracker load --source greenhouse --run-date 2026-10-25
    jobtracker reparse --source greenhouse --start-date 2026-10-20 --end-date 2026-10-25
    jobtracker load-dbt-results --run-date 2026-10-25 --folder /opt/airflow/logs/dbt/2026-10-25

Exit codes: 0 on success, 1 on a failure worth retrying later, and 3 when
the source blocked us. Airflow does not retry a block, since the source is
stopped for the day.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import structlog
from botocore.exceptions import BotoCoreError, ClientError

from jobtracker_ingest.core.http import BlockedError, HttpError, PoliteClient, user_agent_from_env
from jobtracker_ingest.core.logs import configure_logging
from jobtracker_ingest.core.registry import load_registry
from jobtracker_ingest.core.storage import S3ObjectStore
from jobtracker_ingest.extract import ExtractError, detail_needed, run_extract
from jobtracker_ingest.loader.dbt_results import load_dbt_results
from jobtracker_ingest.loader.load import LoadError, run_load
from jobtracker_ingest.loader.reparse import run_reparse
from jobtracker_ingest.loader.runs import NoCompleteRun
from jobtracker_ingest.loader.warehouse import PostgresWarehouse
from jobtracker_ingest.sources import get_adapter

EXIT_FAILED = 1
EXIT_BLOCKED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobtracker")
    commands = parser.add_subparsers(dest="command", required=True)

    extract = commands.add_parser("extract", help="fetch one source and store its payloads")
    extract.add_argument("--source", required=True)
    extract.add_argument("--run-date", required=True, type=date.fromisoformat)
    extract.add_argument(
        "--registry", type=Path, help="path to sources.yml (default: JOBTRACKER_SOURCES)"
    )

    load = commands.add_parser("load", help="parse a stored run into raw.raw_postings")
    load.add_argument("--source", required=True)
    load.add_argument("--run-date", required=True, type=date.fromisoformat)
    load.add_argument(
        "--registry", type=Path, help="path to sources.yml (default: JOBTRACKER_SOURCES)"
    )

    reparse = commands.add_parser(
        "reparse", help="load the stored runs of a date range again after a parser fix"
    )
    reparse.add_argument("--source", required=True)
    reparse.add_argument("--start-date", required=True, type=date.fromisoformat)
    reparse.add_argument("--end-date", required=True, type=date.fromisoformat)
    reparse.add_argument(
        "--registry", type=Path, help="path to sources.yml (default: JOBTRACKER_SOURCES)"
    )

    dbt_results = commands.add_parser(
        "load-dbt-results", help="write the results of a run's dbt steps to ops.dbt_runs"
    )
    dbt_results.add_argument("--run-date", required=True, type=date.fromisoformat)
    dbt_results.add_argument(
        "--folder", required=True, type=Path, help="folder with one artifacts folder per dbt step"
    )
    return parser


def extract(args: argparse.Namespace) -> int:
    log = structlog.get_logger("jobtracker_ingest.cli")
    config = load_registry(args.registry).get(args.source)
    if not config.enabled:
        log.error("source_disabled", source=config.name)
        return EXIT_FAILED

    # Read before the first request, so a database problem costs the site
    # nothing. The connection is not held open while fetching.
    try:
        with PostgresWarehouse.connect() as warehouse:
            known = warehouse.fingerprints(config.name)
    except psycopg.Error as err:
        log.error("extract_failed", source=config.name, error=f"{type(err).__name__}: {err}")
        return EXIT_FAILED

    client = PoliteClient(user_agent_from_env(config.contact), config.min_delay_seconds)
    try:
        run_extract(
            get_adapter(config.name),
            config,
            args.run_date,
            S3ObjectStore.from_env(),
            client,
            needs_detail=detail_needed(known, datetime.now(UTC)),
        )
    except BlockedError as err:
        log.error(
            "extract_blocked",
            source=config.name,
            url=err.url,
            status=err.status,
            error=str(err),
            http=client.stats.as_dict(),
        )
        return EXIT_BLOCKED
    except (ExtractError, HttpError) as err:
        log.error("extract_failed", source=config.name, error=str(err), http=client.stats.as_dict())
        return EXIT_FAILED
    return 0


def load(args: argparse.Namespace) -> int:
    log = structlog.get_logger("jobtracker_ingest.cli")
    # Load reads only what extract stored, so it also works for a source
    # that was disabled since, for example to load its last run again
    config = load_registry(args.registry).get(args.source)
    try:
        with PostgresWarehouse.connect() as warehouse:
            run_load(
                get_adapter(config.name),
                config.name,
                args.run_date,
                S3ObjectStore.from_env(),
                warehouse,
            )
    except (LoadError, NoCompleteRun) as err:
        log.error("load_failed", source=config.name, error=str(err))
        return EXIT_FAILED
    except (psycopg.Error, BotoCoreError, ClientError) as err:
        log.error("load_failed", source=config.name, error=f"{type(err).__name__}: {err}")
        return EXIT_FAILED
    return 0


def reparse(args: argparse.Namespace) -> int:
    log = structlog.get_logger("jobtracker_ingest.cli")
    # Like load, it also works for a source that is not enabled
    config = load_registry(args.registry).get(args.source)
    try:
        with PostgresWarehouse.connect() as warehouse:
            result = run_reparse(
                get_adapter(config.name),
                config.name,
                args.start_date,
                args.end_date,
                S3ObjectStore.from_env(),
                warehouse,
            )
    except ValueError as err:
        log.error("reparse_failed", source=config.name, error=str(err))
        return EXIT_FAILED
    except (psycopg.Error, BotoCoreError, ClientError) as err:
        log.error("reparse_failed", source=config.name, error=f"{type(err).__name__}: {err}")
        return EXIT_FAILED

    if result.failed:
        log.error(
            "reparse_failed",
            source=config.name,
            error=f"{len(result.failed)} days failed to load",
            failed={day.isoformat(): error for day, error in result.failed.items()},
        )
        return EXIT_FAILED
    if not result.loaded:
        log.error("reparse_failed", source=config.name, error="no complete run in the date range")
        return EXIT_FAILED
    if result.rows_inserted == 0:
        # Fine when the same reparse runs again, but a parser fix that left
        # PARSER_VERSION as it was also ends up here
        log.warning("reparse_inserted_nothing", source=config.name)
    return 0


def load_dbt(args: argparse.Namespace) -> int:
    log = structlog.get_logger("jobtracker_ingest.cli")
    # No folder means no dbt step ran, for example when every load failed
    if not args.folder.is_dir():
        log.warning("dbt_results_missing", folder=str(args.folder))
        return 0
    try:
        with PostgresWarehouse.connect() as warehouse:
            rows = load_dbt_results(args.folder, args.run_date, warehouse)
    # A broken artifact gives OSError, ValueError or KeyError
    except (psycopg.Error, OSError, ValueError, KeyError) as err:
        log.error(
            "dbt_results_failed", folder=str(args.folder), error=f"{type(err).__name__}: {err}"
        )
        return EXIT_FAILED
    log.info("dbt_results_loaded", folder=str(args.folder), rows=rows)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    if args.command == "extract":
        return extract(args)
    if args.command == "load":
        return load(args)
    if args.command == "reparse":
        return reparse(args)
    if args.command == "load-dbt-results":
        return load_dbt(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
