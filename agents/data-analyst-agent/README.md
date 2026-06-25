# Data Analyst Agent

Standalone Magenta example for the data-and-list analyst demo. It compares
synthetic `Customer` policy data by pedal cohort, emits Playground artifacts for
MongoDB queries and charts, seeds semantic/taxonomic/episodic/procedural memory,
and uses native LangGraph interrupts for the rating recommendation review loop.

Guardrails are intentionally not used. The review modal is driven by the
LangGraph interrupt payload at `suspend_context.review_presentation`.

## Quick Start

```bash
cp env.example .env
# Fill OPENAI_API_KEY. Fill VOYAGE_API_KEY if you plan to seed memory.
agentic dev up
```

`agentic dev up` does not seed MongoDB automatically. Startup is intentionally
side-effect free because the app can import and hot-reload multiple times across
the local runtime processes. Use `data-analyst-seed-demo` when you want visible
MongoDB data for the demo.

Canonical prompt:

```text
Compare loss frequency across one, two, and three pedal vehicles, controlling for age, zip, and annual mileage. Show me how the cohort mix has changed over time.
```

Useful follow-ups:

```text
Investigate the claims narratives behind the one-pedal result.
What rating action do you recommend?
```

The recommendation follow-up suspends execution for approval. Approving writes
the decision to `audit_log`; rejecting resumes with reviewer feedback.

## Demo Data

The demo has two data modes:

- Smoke test mode: run without seeding. Deterministic in-process `Customer`
  records are generated and are not visible in MongoDB.
- Seeded mode: run the seed scripts after the dev stack is up. This writes
  `Customer`, `catalogs`, and memory records into the local MongoDB-backed
  services.

From the agent directory on the host, after `agentic dev up`, the generated
`.env` contains the local MongoDB URI:

```bash
uv run data-analyst-seed-demo
```

The command replaces the deterministic demo `Customer`, `catalogs`, and
`audit_log` collections so it is safe to run repeatedly. It also seeds
semantic/taxonomic/episodic/procedural memory records when `VOYAGE_API_KEY` is
set. Memory seed data is written to `MONGOMEM_DB_NAME`, the database used by the
MongoMem memory server; keep `MONGOMEM_DB_NAME=agentic_memory_local` in `.env`
for the local dev stack. Use `--skip-memories` to seed only app data, or
`--require-memories` to fail fast when memory cannot be seeded.
The memory seed scripts scope records with `ORG_ID`, `PROJECT_ID`,
`WORKSPACE_ID`, and `DEFAULT_USER_ID`; keep those values aligned with the
runtime values in `.env` so procedural memory lookup can find the seeded
workflow. `ORG_ID` and `PROJECT_ID` must be valid non-zero MongoDB ObjectID hex
strings for the local Agentic Platform proxies.

If you are already inside the running app container, use the runtime virtualenv
managed by the dev entrypoint:

```bash
/tmp/agentic-venvs/.venv-aer-tool/bin/python -m data_analyst_agent.seed_demo
```

From any other shell, set `MONGODB_URI` to the local Mongo URI printed by
`agentic dev up` or `agentic dev status`, then run `uv run data-analyst-seed-demo`.

To inspect the seeded app data, use MongoDB Compass with the dev Mongo URI,
database `data_analyst_agent_local`, and collections `Customer`, `catalogs`, and
`audit_log`. You can also run this from the container, or from any shell with
`MONGODB_URI` exported:

```bash
uv run python - <<'PY'
import os
from pymongo import MongoClient

db = MongoClient(os.environ["MONGODB_URI"])[
    os.environ.get("MONGODB_DATABASE", "data_analyst_agent_local")
]
for name in ("Customer", "catalogs", "audit_log"):
    print(name, db[name].count_documents({}))
PY
```
