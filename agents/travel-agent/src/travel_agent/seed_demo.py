"""Seed the travel-agent demo memories.

This script mirrors what the upstream ``run.sh`` did, adapted to the
``magenta-examples`` conventions. It does two things:

1. Reset and reseed semantic, episodic, and taxonomic memories for the demo
   org so memory recall is meaningful from the very first session.
2. Optionally seed a procedural memory so the agent can replay a learned
   workflow on Session 2 without having to walk through Session 1 first.
   Controlled by ``SEED_PROCEDURAL_MEMORY_ON_STARTUP=true``; defaults to off
   so the demo can show learn-from-resolution behavior.

Usage (inside the dev container)::

    cd /app/agents/travel-agent
    /app/.venv-aer-tool/bin/python -m travel_agent.seed_demo
"""

from __future__ import annotations

import importlib
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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

DEFAULT_USER_ID = "local-dev-user"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_MEMORY_DATABASE = "agentic_memory"
CANONICAL_PROCEDURE_NAME = "travel-disruption-reaccommodation-playbook"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SEED_DIR = PROJECT_ROOT / "memory-seeds"


def _env_file_candidates() -> list[Path]:
    """Return env files in descending precedence order."""
    candidates: list[Path] = []
    explicit_env_file = os.environ.get("TRAVEL_AGENT_ENV_FILE")
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
    """Return (mongo_uri, memory_db_name)."""
    mongo_uri = os.environ.get("MONGODB_URI")
    memory_db = os.environ.get("MONGOMEM_DB_NAME") or DEFAULT_MEMORY_DATABASE
    return mongo_uri, memory_db


def _load_memory_engine_classes() -> tuple[type[Any], type[Any]]:
    """Load embedded mongomem classes from runner-shared.

    The embedded package still uses absolute imports like
    ``mongomem_core.engine`` internally, so add the vendored source directory
    explicitly before importing the engine classes.
    """
    impl_module = importlib.import_module("runner_shared.server.mongomem_impl")
    impl_paths = list(getattr(impl_module, "__path__", []))
    if not impl_paths:
        module_file = getattr(impl_module, "__file__", None)
        raise RuntimeError(
            "runner_shared.server.mongomem_impl must be an embedded package with "
            f"a src/ directory; got {module_file!r}"
        )
    impl_root = Path(impl_paths[0])
    impl_src = impl_root / "src"
    if str(impl_src) not in sys.path:
        sys.path.insert(0, str(impl_src))

    memory_module = importlib.import_module("mongomem_core")
    config_module = importlib.import_module("mongomem_core.config")
    return memory_module.MemoryEngine, config_module.Settings


def _require_id(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} must be set in the environment to seed travel-agent memories")
    return value


def _seeded_user_id() -> str:
    return os.environ.get("SEED_USER_ID", DEFAULT_USER_ID).strip() or DEFAULT_USER_ID


def _load_json(filename: str) -> list[dict[str, Any]]:
    path = SEED_DIR / filename
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        content = handle.read().strip()
    return json.loads(content) if content else []


def _records(filename: str, org_id: str, project_id: str, app_id: str) -> list[dict[str, Any]]:
    records = _load_json(filename)
    user_id = _seeded_user_id()
    for record in records:
        record["org_id"] = org_id
        record["project_id"] = project_id
        record["agent_id"] = app_id
        record.setdefault("user_id", user_id)
    return records


def _reset_seed_documents(
    client: MongoClient,
    db_name: str,
    org_id: str,
    project_id: str,
    app_id: str,
) -> None:
    """Drop prior seeded docs so reruns produce a clean state."""
    db = client[db_name]
    scope = {"org_id": org_id, "project_id": project_id, "agent_id": app_id}

    semantic = _records("semantic.json", org_id, project_id, app_id)
    semantic_sources = sorted(
        {record.get("source", "") for record in semantic if record.get("source")}
    )
    if semantic_sources:
        deleted = db["memory_semantic"].delete_many({**scope, "source": {"$in": semantic_sources}})
        logger.info("Deleted %d existing semantic seed documents", deleted.deleted_count)

    if _load_json("taxonomic.json"):
        deleted = db["memory_taxonomic"].delete_many(scope)
        logger.info("Deleted %d existing taxonomic seed documents", deleted.deleted_count)

    episodic = _load_json("episodic.json")
    titles = [record["title"] for record in episodic if record.get("title")]
    if titles:
        deleted = db["memory_episodic"].delete_many({**scope, "title": {"$in": titles}})
        logger.info("Deleted %d existing episodic seed documents", deleted.deleted_count)

    procedural = _load_json("procedural.json")
    procedures = sorted(
        {
            CANONICAL_PROCEDURE_NAME,
            *(record["procedure"] for record in procedural if record.get("procedure")),
        }
    )
    deleted = db["memory_procedural"].delete_many(
        {
            "org_id": org_id,
            "agent_id": app_id,
            "procedure": {"$in": procedures},
        }
    )
    logger.info("Deleted %d existing procedural seed documents", deleted.deleted_count)


def main() -> None:
    _load_environment()
    mongo_uri, memory_db = _resolve_mongo_settings()
    if not mongo_uri:
        raise SystemExit("MONGODB_URI is required to seed travel-agent demo state")

    org_id = _require_id("ORG_ID")
    project_id = _require_id("PROJECT_ID")
    app_id = _require_id("APP_ID")
    seed_procedural = os.environ.get("SEED_PROCEDURAL_MEMORY_ON_STARTUP", "false").lower() == "true"

    MemoryEngine, Settings = _load_memory_engine_classes()

    embedder = VoyageService()
    embedding_dimension = embedder.dimension or DEFAULT_EMBEDDING_DIMENSION
    settings = Settings(
        mongo_uri=mongo_uri,
        db_name=memory_db,
        embedding_dimension=embedding_dimension,
    )
    engine = MemoryEngine(settings, embedder=cast(Any, embedder))
    engine.bootstrap()

    client: MongoClient = MongoClient(mongo_uri)
    semantic_count = taxonomic_count = episodic_count = procedural_count = 0
    try:
        _reset_seed_documents(client, memory_db, org_id, project_id, app_id)

        for record in _records("semantic.json", org_id, project_id, app_id):
            record["embedding"] = embedder.embed(record["text"])
            engine.create_semantic(**record)
            semantic_count += 1

        for record in _records("taxonomic.json", org_id, project_id, app_id):
            record["embedding"] = embedder.embed(record["definition"])
            engine.create_taxonomic(**record)
            taxonomic_count += 1

        for record in _records("episodic.json", org_id, project_id, app_id):
            record["embedding"] = embedder.embed(record["content"])
            engine.create_episodic(**record)
            episodic_count += 1

        if seed_procedural:
            for record in _records("procedural.json", org_id, project_id, app_id):
                record["embedding"] = embedder.embed(
                    record.get("description") or record.get("content") or record["procedure"]
                )
                engine.create_procedural(**record)
                procedural_count += 1
        else:
            logger.info(
                "Skipping procedural seed (set SEED_PROCEDURAL_MEMORY_ON_STARTUP=true to enable)"
            )

    finally:
        client.close()

    logger.info(
        "Seeded travel-agent demo into %s: %d semantic, %d taxonomic, %d episodic, %d procedural",
        memory_db,
        semantic_count,
        taxonomic_count,
        episodic_count,
        procedural_count,
    )


if __name__ == "__main__":
    main()
