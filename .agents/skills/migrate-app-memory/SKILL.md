---
name: migrate-app-memory
description: Migrate an agent codebase from the legacy app.memory surface (untyped returns, positional writes) to the unified Memory facade (typed results, keyword-only writes). Use when updating a demo/example agent or a customer agent after the app-bound facade change lands, when an agent fails after a rebuild with TypeError on memory calls or attribute errors on memory results, or when someone asks how to adopt the new memory SDK surface inside a deployed agent.
---

# Migrate app.memory to the unified Memory facade

The unified facade replaces the previous `app.memory` surface; the change is
what `app.memory` methods accept and return. Agent code written against the legacy surface needs the mechanical edits below. The migrated insurance agent (both monorepo copies, listed under the sdklanggraph README's "Reference migrations") is the worked example; the README's "Migrating from the pre-facade surface" section is the canonical delta list.

## Step 1: Find every call site

```bash
grep -rn "app\.memory\.\|\.memory\." --include="*.py" src/ | grep -v test
```

Every hit needs review against the checklist below. Memory methods: `save_semantic`, `search_semantic`, `get_semantic`, `save_episode`, `search_episodes`, `list_episodes`, `save_taxonomic`, `search_taxonomic`, `get_taxonomic_term`, `list_domains`, `save_procedure`, `discover_procedures`, `get_procedure`, `build_context`, plus (new on the facade) `record_turn`, `search`, `bind`.

## Step 2: Apply the mechanical changes

The canonical delta list lives in the sdklanggraph README under "Migrating from
the pre-facade surface" — if the surface changes, update it there first. Work each call site against that list; symptom-to-fix
triage:

| Symptom | Fix |
|---|---|
| `TypeError` on a write call | writes are keyword-only: `save_semantic(text=..., label=...)` |
| `if result:` always true, or dict access fails | typed results: `result.acknowledged`, `result.id`; searches return `MemoryChunk` (`chunk.content`, `chunk.similarity_score`, loose keys under `chunk.metadata`) |
| `build_context` result used as a string | use `response.formatted_context` |
| `MemoryIdentityError` outside an invoke | pass identity explicitly or `bind()`; episodic writes need a resolvable session |
| Result counts changed | per-type search `top_k` default went 10 → 50; pin explicitly if the prompt budget depends on it |
| Private reads return fewer results | `visibility="private"` now keeps the ambient user filter; pass `user_id` explicitly for the old broad read |

## Step 3: Verify

1. `uv run pytest` (or the agent's test command) — TypeErrors and attribute errors surface immediately.
2. Deploy locally (`agentic dev up` or the agent-local-deploy skill) and exercise one write + one read + one `build_context` through a real conversation turn.
3. Confirm no bare-`bool` checks remain: `grep -rn "if app.memory.save" src/` should show `.acknowledged` usages only.

## Notes

- Nothing server-side changed: no redeploy of platform components, no data migration. The new surface arrives with the agent's next build against base images that carry the facade.
- Reads that only iterate content strings (`for m in results: m.content`) often need no change beyond dict→attribute access.
- The standalone SDK (`agentic_platform_memory.Memory` with an API key) already had this surface; code shared between standalone and app-bound modes migrates once and works in both.
