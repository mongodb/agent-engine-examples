"""Seed the data analyst demo with one command."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from data_analyst_agent import seed_memories
from data_analyst_agent.data_store import (
    DEFAULT_DATABASE,
    DemoDataStore,
    generate_customer_records,
    load_customer_snapshot,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_GENERATED_SCALE = 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scale",
        type=int,
        default=DEFAULT_GENERATED_SCALE,
        help=(
            "Multiplier for deterministic generated Customer fixture size. "
            "The checked-in Customer snapshot is used when this remains at the default."
        ),
    )
    parser.add_argument(
        "--skip-memories",
        action="store_true",
        help="Seed only Customer/catalog data and skip long-term memory records.",
    )
    parser.add_argument(
        "--require-memories",
        action="store_true",
        help="Fail if memory records cannot be seeded.",
    )
    args = parser.parse_args(argv)
    if args.skip_memories and args.require_memories:
        parser.error("--skip-memories and --require-memories cannot be used together")
    return args


def _load_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env.dev", override=False)
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _seed_app_data(*, mongo_uri: str, database_name: str, reset: bool, scale: int) -> int:
    records = _load_seed_records(scale=scale)
    store = DemoDataStore(
        mongodb_uri=mongo_uri,
        database_name=database_name,
        records=records,
    )
    try:
        store.seed(reset=reset, records=records)
    finally:
        store.close()
    logger.info("Seeded %d Customer records into %s", len(records), database_name)
    return len(records)


def _load_seed_records(*, scale: int) -> list[dict[str, object]]:
    if scale == DEFAULT_GENERATED_SCALE:
        snapshot_records = load_customer_snapshot()
        if snapshot_records:
            logger.info(
                "Loaded %d Customer records from checked-in snapshot", len(snapshot_records)
            )
            return snapshot_records

    records = generate_customer_records(scale=scale)
    logger.info("Generated %d Customer records", len(records))
    return records


def main(argv: Sequence[str] | None = None) -> None:
    _load_environment()
    args = parse_args(argv)

    mongo_uri = os.environ.get("MONGODB_URI", "")
    if not mongo_uri:
        raise SystemExit("MONGODB_URI is required to seed the data analyst demo")

    database_name = os.environ.get("MONGODB_DATABASE", DEFAULT_DATABASE)
    should_seed_memories = not args.skip_memories
    has_voyage_key = bool(os.environ.get("VOYAGE_API_KEY"))
    if should_seed_memories and args.require_memories and not has_voyage_key:
        raise SystemExit("VOYAGE_API_KEY is required to seed demo memories")
    if should_seed_memories and has_voyage_key and not os.environ.get("MONGOMEM_DB_NAME"):
        raise SystemExit("MONGOMEM_DB_NAME is required to seed demo memories")

    customer_count = _seed_app_data(
        mongo_uri=mongo_uri,
        database_name=database_name,
        reset=True,
        scale=args.scale,
    )

    if not should_seed_memories:
        logger.info("Skipped memory seeding by request")
        return

    if not has_voyage_key:
        logger.warning("Skipped memory seeding because VOYAGE_API_KEY is not set")
        return

    seed_memories.main()
    logger.info("Demo seed complete with %d Customer records and memory records", customer_count)


if __name__ == "__main__":
    main()
