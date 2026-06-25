"""Seed the pricing analyst demo memories into MongoDB."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from pymongo import MongoClient
from runner_shared.voyage import VoyageService

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

ORG_ID = "org_pricing_demo"
DEFAULT_USER_ID = "507f1f77bcf86cd799439012"
DEFAULT_EMBEDDING_DIMENSION = 1024
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SEED_DIR = PROJECT_ROOT / "memory-seeds"


def _env_file_candidates() -> list[Path]:
    """Return env files in descending precedence order.

    Precedence is:
    1. Already-exported process environment
    2. Explicit env file via PRICING_ANALYST_ENV_FILE
    3. Local host/dev overrides in .env.dev
    4. Repo default .env
    """
    candidates: list[Path] = []
    explicit_env_file = os.environ.get("PRICING_ANALYST_ENV_FILE")
    if explicit_env_file:
        explicit_path = Path(explicit_env_file)
        if not explicit_path.is_absolute():
            explicit_path = PROJECT_ROOT / explicit_path
        candidates.append(explicit_path)

    candidates.extend([PROJECT_ROOT / ".env.dev", PROJECT_ROOT / ".env"])

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(path)
    return ordered


def _load_environment() -> None:
    """Load local env files without overriding already-exported variables."""
    for env_file in _env_file_candidates():
        if env_file.exists():
            load_dotenv(env_file, override=False)


def _resolve_mongo_settings() -> tuple[str | None, str]:
    """Resolve MongoDB connection settings for memory seeding."""
    mongo_uri = os.environ.get("MONGODB_URI")
    memory_db = os.environ.get("MDB_AGENTIC_STORE_DB") or os.environ.get(
        "MEMORY_DATABASE", "memory"
    )
    return mongo_uri, memory_db


def _load_json(filename: str) -> list[dict[str, Any]]:
    path = SEED_DIR / filename
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        content = handle.read().strip()
    return json.loads(content) if content else []


def _semantic_records() -> list[dict[str, Any]]:
    records = _load_json("semantic.json")
    for record in records:
        record.setdefault("org_id", ORG_ID)
        record.setdefault("user_id", DEFAULT_USER_ID)
    return records


def _taxonomic_records() -> list[dict[str, Any]]:
    records = _load_json("taxonomic.json")
    for record in records:
        record.setdefault("org_id", ORG_ID)
        record.setdefault("user_id", DEFAULT_USER_ID)
    return records


def _episodic_records() -> list[dict[str, Any]]:
    records = _load_json("episodic.json")
    for record in records:
        record.setdefault("org_id", ORG_ID)
        record.setdefault("user_id", DEFAULT_USER_ID)
    return records


def _reset_seed_documents(client: MongoClient, db_name: str) -> None:
    semantic = _semantic_records()
    taxonomic = _taxonomic_records()
    episodic = _episodic_records()
    db = client[db_name]

    semantic_sources = sorted(
        {record.get("source", "") for record in semantic if record.get("source")}
    )
    if semantic_sources:
        deleted = db["memory_semantic"].delete_many(
            {"org_id": ORG_ID, "source": {"$in": semantic_sources}}
        )
        logger.info("Deleted %d existing semantic seed documents", deleted.deleted_count)

    if taxonomic:
        deleted = db["memory_taxonomic"].delete_many({"org_id": ORG_ID})
        logger.info("Deleted %d existing taxonomic seed documents", deleted.deleted_count)

    if episodic:
        titles = [record["title"] for record in episodic if record.get("title")]
        deleted = db["memory_episodic"].delete_many({"org_id": ORG_ID, "title": {"$in": titles}})
        logger.info("Deleted %d existing episodic seed documents", deleted.deleted_count)


def main() -> None:
    _load_environment()
    mongo_uri, memory_db = _resolve_mongo_settings()
    if not mongo_uri:
        raise SystemExit("MONGODB_URI is required to seed demo memories")

    from mongomem_core import MemoryEngine
    from mongomem_core.config import Settings

    embedder = VoyageService()
    embedding_dimension = embedder.dimension or DEFAULT_EMBEDDING_DIMENSION
    settings = Settings(
        mongo_uri=mongo_uri,
        db_name=memory_db,
        embedding_dimension=embedding_dimension,
    )
    engine = MemoryEngine(settings, embedder=cast(Any, embedder))
    engine.bootstrap()

    client = MongoClient(mongo_uri)
    try:
        _reset_seed_documents(client, memory_db)

        semantic_count = 0
        for record in _semantic_records():
            record["embedding"] = embedder.embed(record["text"])
            engine.create_semantic(**record)
            semantic_count += 1

        taxonomic_count = 0
        for record in _taxonomic_records():
            record["embedding"] = embedder.embed(record["definition"])
            engine.create_taxonomic(**record)
            taxonomic_count += 1

        episodic_count = 0
        for record in _episodic_records():
            record["embedding"] = embedder.embed(record["content"])
            engine.create_episodic(**record)
            episodic_count += 1
    finally:
        client.close()

    logger.info(
        "Seeded pricing analyst demo memories into %s: %d semantic, %d taxonomic, %d episodic",
        memory_db,
        semantic_count,
        taxonomic_count,
        episodic_count,
    )


if __name__ == "__main__":
    main()
