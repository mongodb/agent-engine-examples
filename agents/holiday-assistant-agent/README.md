# Holiday Assistant Agent

A multi-skill holiday-planning agent built on top of `atlasap-sdklanggraph`.
The agent combines three specialist competencies in a single LangGraph:

- **Hotels** — semantic + structured search of accommodation, room availability,
  hotel bookings (create / look up / cancel).
- **Transport** — flights, trains, coaches, ferries and last-mile transfers,
  plus transport bookings.
- **Policy & Compliance** — booking rules, cancellation terms, child/pet
  policies, accessibility, data protection, travel insurance guidance, and
  compliance checks against a user's planned trip.

It is a Python port of the
[multi-agent-holiday-assistant-mongodb](https://github.com/) reference app,
adapted to the atlasap conventions (single graph, atlasap `App`,
`@app.tool` decorators).

## Quick start

```bash
# 1) Set up environment (already populated for local Atlas + Voyage)
cp env.example .env  # if you don't already have one

# 2) Install dependencies
uv sync

# 3) Seed the local MongoDB cluster with sample hotels and policies
uv run holiday-assistant-seed

# 4) Run the agent locally with the agentic CLI
agentic dev up --workspace holiday-assistant-agent

# 5) Invoke it (the OE port is printed by `agentic dev up`)
curl -X POST 'http://localhost:<OE_PORT>/invoke' \
  -H 'Content-Type: application/json' \
  -d '{"message": "Find me a 4-star hotel in Barcelona under €200/night"}'
```

`agentic dev status` will print the OE port if you've forgotten it. The
`agentic invoke` CLI command targets *deployed* agents on the platform and
requires `agentic auth login`; for local-dev iteration use the curl form
above.

## Environment

| Variable | Purpose |
|----------|---------|
| `MONGODB_URI` | MongoDB connection (defaults to `mongodb://127.0.0.1:27015/?directConnection=true`) |
| `HOLIDAY_DATABASE` | Database name for hotels / policies / bookings (default `holiday_db`) |
| `MONGODB_DATABASE` | Database for agent checkpoints (default `agent_memory`) |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `CEREBRAS_API_KEY` | LLM provider (any one) |
| `VOYAGE_API_KEY` | Optional. When set the seed script generates Voyage embeddings and creates Atlas vector search indexes. Without it the agent falls back to keyword matching. |

## Example queries

- *Hotels:* "Find me a beach resort in the Algarve under €600/night",
  "What rooms does the Hilton Barcelona have?",
  "Book a deluxe room at Le Meurice Paris from 10–15 Aug for Alice Smith",
  "Cancel booking AB12CD34".
- *Transport:* "How do I get from London to Barcelona?",
  "Cheapest way from Paris to Amsterdam",
  "How do I get from Santorini Airport to Oia?".
- *Policy:* "What is your cancellation policy?",
  "Can I bring my dog to the resort?",
  "Is booking AB12CD34 compliant with the child policy?".

## Demos

- **[DEMO.md](./DEMO.md)** — a 7–10 minute presenter walkthrough for a
  live audience. Use the playground UI at http://localhost:3000 with
  copy-paste prompts, talk track, and Approve/Reject button cues for
  the HITL section.
- **`demo.py`** — automated end-to-end test that drives the same
  scenarios via the OE invoke/resume API and asserts on user-visible
  behavior. Use it as a smoke test before a presentation, or in CI.

  ```bash
  agentic dev up --workspace holiday-assistant-agent  # one-time
  uv run holiday-assistant-seed                        # one-time
  uv run python demo.py                                # ~3 minutes
  uv run python demo.py --only-section hitl            # just the HITL flow
  ```

## Cancellation HITL flow

Most cancellations cancel instantly. Four boutique / luxury properties in
the seed data (Le Meurice, Katikies, Mystique, Belmond Caruso) are flagged
`requires_cancellation_approval: true`. For those, `cancel_booking` is a
two-call contract:

1. The first call (without `user_confirmed_hotel_contact=true`) returns
   `status: needs_user_confirmation` and does NOT suspend. The agent must
   tell the guest the hotel handles cancellations directly and ask
   permission to submit on their behalf.
2. The second call (with `user_confirmed_hotel_contact=true`) suspends
   the agent with a review request that goes to the hotel.

This guarantees the user is informed *before* the suspend pause, even if
the LLM would otherwise have skipped the heads-up turn.
