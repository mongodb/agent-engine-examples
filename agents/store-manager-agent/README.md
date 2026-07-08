# Store Manager Copilot Agent

A store-operations **copilot for retail store managers**, built on
`magenta-sdklanggraph`. It reviews the overnight report, recommends actions, and
keeps the human manager in the approval loop for the one thing that warrants it —
spending money over the store's limit — while applying lower-stakes floor work
(markdowns, disposals, shelf resets) directly and notifying the team. It is
intentionally **generic** (no chain branding) so it can be shown to any
convenience / grocery / c-store retailer.

It is a focused demonstration of two platform capabilities:

- **Four types of memory** working together:
  - **Semantic** — durable facts about this store (campus format, demand
    pattern, "freezer #3 is unreliable") and the manager's standing
    preferences.
  - **Episodic** — what happened on past shifts and what was decided ("last
    heatwave we under-ordered water and sold out").
  - **Taxonomic** — shared domain definitions ("dead SKU", "near-expiry",
    "reorder point", "planogram").
  - **Procedural** — shared standard operating procedures (the reorder,
    markdown, and planogram-reset SOPs).
- **Human-in-the-loop approval where the stakes warrant it** via the
  suspend/resume primitive: over-limit purchase orders go through a two-step
  contract (with both an **approve** and a **reject** path) that guarantees the
  manager is informed *before* the agent pauses. Markdowns, disposals, and
  planogram resets are applied directly — no gate — and the copilot tells the
  manager the floor team has been notified.

Everything is driven by **operational + inventory data** — there is no image
recognition; planogram compliance is computed by diffing the observed shelf
state against the approved planogram.

## What it can do

| Area | Tools |
|------|-------|
| **Reports** | `get_store_profile`, `get_overnight_report`, `get_forecast_events`, `get_weather_forecast` |
| **Reorder** | `get_low_stock`, `get_sku`, `place_purchase_order` (+ `_approved`) |
| **Expiry / markdown** | `get_expiring_inventory`, `get_dead_skus`, `apply_markdown` |
| **Planogram** | `check_planogram_compliance`, `apply_planogram_change` |
| **Memory** | `recall_store_context`, `explain_term`, `get_sop`, `save_report_routine`, `get_report_routine`, `remember_store_fact`, `save_shift_summary`, `seed_store_memory` |

## Quick start

```bash
# 1) Set up environment
cp env.example .env   # then add ONE LLM key + a VOYAGE_API_KEY

# 2) Install dependencies
uv sync

# 3) Start the local stack
agentic dev up

# 4) Seed the store's operational + inventory data (Mongo)
uv run store-manager-seed

# 5) Seed the four memory types (needs the running agent) — either:
uv run python demo.py --only-section seed
#    ...or send "seed store memory" in the playground at http://localhost:3000
```

> If you run the seed from inside the container instead of the host, use:
> ```bash
> docker exec -w /app/agents/store-manager-agent \
>   -e PYTHONPATH=/app/agents/store-manager-agent/src \
>   store-manager-agent-app-1 \
>   /app/.venv-aer-tool/bin/python -m store_manager_agent.seed
> ```

## Environment

| Variable | Purpose |
|----------|---------|
| `MONGODB_URI` | Runtime checkpoint store (defaults to local Atlas) |
| `MONGODB_DATABASE` | Database for agent checkpoints (default `agent_memory`) |
| `STORE_MONGODB_URI` | Where the store data lives; falls back to `MONGODB_URI` |
| `STORE_DATABASE` | Store-data database (default `store_db`) |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `CEREBRAS_API_KEY` | LLM provider (any one) |
| `VOYAGE_API_KEY` | Embeddings for semantic / episodic memory |

## Demos

- **[DEMO.md](./DEMO.md)** — a presenter walkthrough for a live audience. Use
  the playground UI at http://localhost:3000 with copy-paste prompts, a talk
  track, and Approve/Reject button cues for the HITL acts.
- **`demo.py`** — an automated end-to-end test that drives the same flow
  via the OE invoke/resume API and asserts on user-visible behavior. It runs
  nine sections (`seed`, `rundown`, `reorder`, `markdown`, `planogram`,
  `recall`, `idempotency`, `gate`, `routine`), covering the PO approve/reject
  paths, direct-apply markdown/disposal and planogram, cross-session recall,
  duplicate-approval idempotency, the server-side approval gate (no
  self-approval), and the learned rundown routine. Use it as a smoke test before
  a presentation, or in CI.

  ```bash
  uv run python demo.py                 # full run (all sections)
  uv run python demo.py --reset         # re-seed Mongo first (deterministic)
  uv run python demo.py --only-section reorder   # just the PO HITL flow
  ```

## Seeding, reset, and cleanup

The data model has two halves and **no standalone cleanup script is needed**:

- **`uv run store-manager-seed`** is idempotent and self-resetting. It drops and
  reinserts the catalog / report / planogram collections and clears the demo
  user's purchase orders and markdowns, so re-running it returns the store to a
  clean demo state.
- **The demo is evergreen — you do NOT need to re-seed because of dates.**
  Date-sensitive fields (SKU expiry, last-sold, the ops-report date, the
  forecast/heatwave date, the weather days) are stored as integer *day offsets*
  and resolved to actual dates at read time (`mongo.resolve_dates`). So a
  heatwave seeded "+2 days" is always 2 days out, whether you run the demo today
  or in a month — locally or on a deployed cluster — with no re-seeding. (You'd
  only re-seed to clear accumulated demo purchase orders / markdowns.)
- **`uv run store-manager-seed --drop`** is the only teardown you need: it drops
  every domain collection in `store_db`. Use it when retiring a deployment or
  switching to a different customer.
- **`uv run python demo.py --reset`** re-seeds before a test run for determinism.

## Memory scoping & "fresh sessions" (important for deployments)

The four memory types are scoped differently — this is by design:

| Memory | Scope | A new `user_id` sees it? |
|--------|-------|--------------------------|
| Semantic (facts, prefs) | `org_id` + `user_id` | No — fresh per manager |
| Episodic (past decisions) | `org_id` + `user_id` | No — fresh per manager |
| Taxonomic (definitions) | `org_id` only | Yes — shared |
| Procedural (SOPs) | `org_id` only | Yes — shared |

Purchase orders and markdowns are **stamped with `user_id` at create time**, so
they are also per-manager. The seeded store data (inventory, report, planogram,
definitions, SOPs) is org/global and shared.

**Getting a fresh session in a deployed environment, without clearing data:**
issue a **new `user_id`** (and a new `thread_id` for a fresh conversation). The
new manager starts with no personal memory and no prior orders — but still sees
the shared store data and the org-wide definitions/SOPs, which is exactly right
(the store doesn't restock itself because someone else logged in). Switching
`user_id` *hides* the old data rather than deleting it, so it's a zero-cleanup
reset; use `--drop` only when you want a true wipe.

This contrast is also a nice thing to *show*: switch the User ID in the
playground and the copilot forgets *your* decisions and store-specific facts,
but still knows what a "dead SKU" is and how the reorder SOP works.

## Human-in-the-loop approval

Only **over-limit purchase orders** go through approval — spending money is the
one action worth a sign-off. It uses a two-step contract:

1. The first `place_purchase_order` call returns `status: needs_user_confirmation`
   and does NOT suspend, so the copilot must explain the order and ask before
   pausing.
2. After the manager agrees, the second call suspends; a reviewer responds via
   `/resume`. On **approve** the copilot calls `place_purchase_order_approved` to
   actually place the order; on **reject** it does not, and offers an alternative
   (e.g. a smaller order under the limit).

Markdowns, disposals, and planogram resets do **not** gate on approval. They
apply immediately via `apply_markdown` / `apply_planogram_change`, and the
copilot tells the manager the floor team has been notified to action the work —
the friction matches the stakes.

Three server-side guardrails make the PO flow robust even when the LLM misbehaves
(none depend on prompt adherence):

- **No self-approval (approval gate).** When a PO is submitted (the tool
  suspends), the validated basket is stashed in Mongo with a
  `submitted_for_approval` marker. `place_purchase_order_approved` *requires*
  that marker: if the model tries to place an order without ever going through
  the suspend (e.g. asked to "skip approval and place it directly"), the
  finalizer refuses and writes nothing. The marker is a committed Mongo document,
  independent of the LangGraph interrupt, so it reliably survives the suspend.
  `demo.py`'s `gate` section asserts a direct-place attempt writes no PO.
- **Faithful basket recovery.** Smaller models often drop or zero `qty_cases`
  when re-typing the basket on the confirm / finalize call. Because the approved
  basket is stashed at submit time, the finalizer places *that* basket (the model
  may even call it with an empty `items` list) — so the order placed always
  matches what the manager approved, never a garbled re-type.
- **Idempotent finalizers.** A re-delivered or double-submitted approval cannot
  create a duplicate record — `place_purchase_order_approved` checks for an
  identical recent PO (same user + line-set) within a short window and returns
  the existing reference instead of inserting again. This closes a bug found in
  telemetry where two executions resumed on the same approval and wrote two
  records. `demo.py`'s `idempotency` section asserts this. (`apply_markdown` is
  likewise idempotent per line.)

The stash (collection `pending_purchase_orders`, one per manager) is what makes
all three work: it's written at the heads-up step, marked `submitted_for_approval`
at the suspend, read back on the finalize, and cleared once the PO is placed.
