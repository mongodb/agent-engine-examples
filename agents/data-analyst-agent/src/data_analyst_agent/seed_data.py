"""Seed the data analyst demo Customer and catalog collections."""

from __future__ import annotations

import argparse
import logging
import os

from dotenv import load_dotenv

from data_analyst_agent.data_store import (
    DEFAULT_DATABASE,
    DemoDataStore,
    generate_customer_records,
    load_customer_snapshot,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data first.")
    parser.add_argument(
        "--scale",
        type=int,
        default=2,
        help=(
            "Multiplier for deterministic generated Customer fixture size. "
            "The checked-in Customer snapshot is used when this remains at the default."
        ),
    )
    parser.add_argument(
        "--generated",
        action="store_true",
        help="Use the deterministic generated fixture instead of the checked-in Customer snapshot.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(".env.dev", override=False)
    load_dotenv(override=False)

    mongo_uri = os.environ.get("MONGODB_URI", "")
    database_name = os.environ.get("MONGODB_DATABASE", DEFAULT_DATABASE)
    if not mongo_uri:
        raise SystemExit("MONGODB_URI is required to seed demo data")

    args = parse_args()
    records = (
        generate_customer_records(scale=args.scale)
        if args.generated
        else load_customer_snapshot() or generate_customer_records(scale=args.scale)
    )
    store = DemoDataStore(
        mongodb_uri=mongo_uri,
        database_name=database_name,
        records=records,
    )
    try:
        store.seed(reset=args.reset, records=records)
    finally:
        store.close()

    logger.info("Seeded %d Customer records into %s", len(records), database_name)


if __name__ == "__main__":
    main()
