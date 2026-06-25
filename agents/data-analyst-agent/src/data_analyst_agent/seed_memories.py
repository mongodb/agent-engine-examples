"""Seed data analyst demo memories into MongoDB."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from pymongo import MongoClient
from runner_shared.voyage import VoyageService

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

ORG_ID = "000000000000000000000001"
PROJECT_ID = "000000000000000000000002"
AGENT_ID = "data-analyst-agent"
DEFAULT_USER_ID = "local-dev-user"
DEFAULT_EMBEDDING_DIMENSION = 1024
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SEED_DIR = PROJECT_ROOT / "memory-seeds"


def _env_file_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit_env_file = os.environ.get("DATA_ANALYST_ENV_FILE")
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
    for env_file in _env_file_candidates():
        if env_file.exists():
            load_dotenv(env_file, override=False)


def _resolve_mongo_settings() -> tuple[str | None, str]:
    mongo_uri = os.environ.get("MONGODB_URI")
    memory_db = os.environ.get("MONGOMEM_DB_NAME")
    if not memory_db:
        raise SystemExit("MONGOMEM_DB_NAME is required to seed demo memories")
    return mongo_uri, memory_db


def _ensure_mongomem_core_import_path() -> None:
    from runner_shared.server import mongomem_impl

    mongomem_src = Path(mongomem_impl.__file__).resolve().parent / "src"
    mongomem_src_path = str(mongomem_src)
    if mongomem_src_path not in sys.path:
        sys.path.insert(0, mongomem_src_path)


def _identity_defaults() -> dict[str, str]:
    return {
        "org_id": os.environ.get("ORG_ID", ORG_ID),
        "project_id": os.environ.get("PROJECT_ID", PROJECT_ID),
        "agent_id": os.environ.get("WORKSPACE_ID", AGENT_ID),
        "user_id": os.environ.get("DEFAULT_USER_ID", DEFAULT_USER_ID),
    }


def _identity_filter() -> dict[str, str]:
    defaults = _identity_defaults()
    return {
        "org_id": defaults["org_id"],
        "project_id": defaults["project_id"],
        "agent_id": defaults["agent_id"],
        "user_id": defaults["user_id"],
    }


def _load_json(filename: str) -> list[dict[str, Any]]:
    path = SEED_DIR / filename
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        content = handle.read().strip()
    return json.loads(content) if content else []


def _with_runtime_identity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    defaults = _identity_defaults()
    for record in records:
        record["org_id"] = defaults["org_id"]
        record["project_id"] = defaults["project_id"]
        record["agent_id"] = defaults["agent_id"]
        record["user_id"] = defaults["user_id"]
    return records


def _semantic_records() -> list[dict[str, Any]]:
    return _with_runtime_identity(_load_json("semantic.json"))


def _taxonomic_records() -> list[dict[str, Any]]:
    return _with_runtime_identity(_load_json("taxonomic.json"))


def _episodic_records() -> list[dict[str, Any]]:
    return _with_runtime_identity(_load_json("episodic.json"))


def _procedural_records() -> list[dict[str, Any]]:
    return _with_runtime_identity(_load_json("procedural.json"))


def _reset_seed_documents(client: MongoClient, db_name: str) -> None:
    db = client[db_name]
    identity_filter = _identity_filter()

    semantic_labels = [record["label"] for record in _semantic_records() if record.get("label")]
    if semantic_labels:
        deleted = db["memory_semantic"].delete_many(
            {**identity_filter, "label": {"$in": semantic_labels}}
        )
        logger.info("Deleted %d existing semantic seed documents", deleted.deleted_count)

    taxonomic_terms = [record["term"] for record in _taxonomic_records() if record.get("term")]
    if taxonomic_terms:
        deleted = db["memory_taxonomic"].delete_many(
            {**identity_filter, "term": {"$in": taxonomic_terms}}
        )
        logger.info("Deleted %d existing taxonomic seed documents", deleted.deleted_count)

    episodic_titles = [record["title"] for record in _episodic_records() if record.get("title")]
    if episodic_titles:
        deleted = db["memory_episodic"].delete_many(
            {**identity_filter, "title": {"$in": episodic_titles}}
        )
        logger.info("Deleted %d existing episodic seed documents", deleted.deleted_count)

    procedures = [
        record["procedure"] for record in _procedural_records() if record.get("procedure")
    ]
    if procedures:
        deleted = db["memory_procedural"].delete_many(
            {**identity_filter, "procedure": {"$in": procedures}}
        )
        logger.info("Deleted %d existing procedural seed documents", deleted.deleted_count)


def _embed(embedder: VoyageService, text: str) -> list[float]:
    return cast(list[float], embedder.embed(text))


def main() -> None:
    _load_environment()
    mongo_uri, memory_db = _resolve_mongo_settings()
    if not mongo_uri:
        raise SystemExit("MONGODB_URI is required to seed demo memories")

    _ensure_mongomem_core_import_path()
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
            record["embedding"] = _embed(embedder, record["text"])
            engine.create_semantic(**record)
            semantic_count += 1

        taxonomic_count = 0
        for record in _taxonomic_records():
            record["embedding"] = _embed(embedder, record["definition"])
            engine.create_taxonomic(**record)
            taxonomic_count += 1

        episodic_count = 0
        for record in _episodic_records():
            record["embedding"] = _embed(embedder, record["content"])
            engine.create_episodic(**record)
            episodic_count += 1

        procedural_count = 0
        for record in _procedural_records():
            record["embedding"] = _embed(embedder, record["content"])
            engine.create_procedural(**record)
            procedural_count += 1
    finally:
        client.close()

    logger.info(
        "Seeded data analyst memories into %s: %d semantic, %d taxonomic, %d episodic, %d procedural",
        memory_db,
        semantic_count,
        taxonomic_count,
        episodic_count,
        procedural_count,
    )


if __name__ == "__main__":
    main()
