# Travel Agent Template

This template gives you a compact app that searches a database of flights and
listings through MongoDB's managed MCP server, creates bookings, suspends for human
review before a booking is written, and remembers traveler preferences across
conversations.

**Features:** Memory (wired, on by default) · Human-in-the-loop (suspend/resume) ·
MongoDB Remote MCP

## What This Template Includes

- `project-config.yaml` at the project root, with memory settings shared by all
  workspaces, and the agent in the `agents/trip-planner/` workspace
- Flight and listing search through MongoDB's managed MCP server, configured under
  `mcp` in `agents/trip-planner/agent.yaml`
- `create_booking` and `list_bookings` tools
- Long-term memory for traveler preferences
- Sample data in `agents/trip-planner/data/` and a loader in
  `agents/trip-planner/scripts/`

The LLM client is defined in `agents/trip-planner/src/trip_planner/llm.py` and reads
`LLM_API_KEY`. Memory extraction uses the same key.

### How the agent reads MongoDB

The agent uses four tools from the MongoDB MCP server: `remote-atlas-connect`,
`list-collections`, `collection-schema`, and `aggregate`. It connects to the
cluster set by `ATLAS_PROJECT_ID` and `ATLAS_CLUSTER_NAME`.

The MCP server can read any data its Atlas configuration can reach, including
other travelers' bookings. To prevent that, `restrict_to_catalog` in `tools.py`
checks each MCP call and:

- allows reads of only the `flights` and `listings` collections
- rejects stages such as `$lookup`, `$unionWith`, and `$out`
- limits aggregations to 20 results

Travelers see their own bookings through `list_bookings`.

The MCP server only connects to Atlas, so local development also uses your Atlas
cluster instead of a local MongoDB.

## Before You Start

1. Have an Atlas cluster. Note its project ID and cluster name.
2. Create an Atlas Managed MCP configuration: in the Atlas UI, go to
   **Project Access Manager** > **Service Accounts**, then click **Add new** in the
   **Atlas Managed MCP** card. Give it the **Project Data Access Read Only** role;
   **Project Read Only** can't read data. Copy the client ID and client secret;
   Atlas shows the secret only once. See
   [Get Started with the MongoDB MCP Server](https://www.mongodb.com/docs/mcp-server/get-started/).

## Quick Start

```bash
cd agents/trip-planner
cp env.example .env
```

Edit `agents/trip-planner/.env` and set:

- `MONGODB_URI` to your Atlas connection string
- `ATLAS_PROJECT_ID` and `ATLAS_CLUSTER_NAME` for the same cluster
- `MDB_MCP_CLIENT_ID` and `MDB_MCP_CLIENT_SECRET` from your MCP configuration
- `LLM_API_KEY` for the LLM client
- `VOYAGE_API_KEY` for memory

To switch LLM providers, update the import, constructor, and `MODEL` in
`src/trip_planner/llm.py`, `extraction_llm` in `project-config.yaml`, and the LLM
host under `sandboxes.tool.network.egress` in `agent.yaml`.

Load the sample data into your cluster:

```bash
uv run scripts/seed_data.py
```

Then start the local stack. It reads memory settings from `project-config.yaml` in
the project root:

```bash
agentengine dev up
```

Open the Playground UI at `http://localhost:3000` to chat with the agent. Stop the
local stack with `agentengine dev down`.

## Deploy

From `agents/trip-planner/`, register the workspace and link your cluster. Choose the
project that contains your Atlas cluster:

```bash
agentengine init
agentengine atlas setup
```

`agentengine atlas setup` stores `MONGODB_URI` and `VOYAGE_API_KEY` as project
secrets. Store the remaining secrets and the memory settings:

```bash
agentengine secret set LLM_API_KEY
agentengine secret set MONGODB_DATABASE --workspace-scope
agentengine secret set ATLAS_PROJECT_ID --workspace-scope
agentengine secret set ATLAS_CLUSTER_NAME --workspace-scope
agentengine secret set MDB_MCP_CLIENT_ID --workspace-scope
agentengine secret set MDB_MCP_CLIENT_SECRET --workspace-scope
agentengine memory configure
```

Then build and deploy:

```bash
agentengine build
agentengine deploy
```

## Project Layout

```text
travel-agent/                  # project root
├── project-config.yaml        # memory settings shared by all workspaces
├── README.md
└── agents/
    └── trip-planner/          # workspace
        ├── agent.yaml
        ├── dev.yaml
        ├── env.example
        ├── pyproject.toml
        ├── pyrightconfig.json
        ├── uv.toml
        ├── data/
        │   ├── flights.json
        │   └── listings.json
        ├── scripts/
        │   └── seed_data.py
        └── src/trip_planner/
            ├── __init__.py
            ├── llm.py
            ├── main.py
            ├── system_message.py
            └── tools.py
```
