# Insurance Agent (Magenta SDK)

A comprehensive insurance assistant built on the **Magenta SDK** demonstrating the complete insurance lifecycle: customer acquisition, policy management, claims processing, and human-in-the-loop dispute resolution

## Features

### Policy Management
- **Policy Lookup**: Look up insurance policies by number
- **Quotes**: Get personalized insurance quotes with loyalty discounts
- **Policy Creation**: Create new auto insurance policies
- **Policy Listing**: List all policies for a customer

### Claims Processing
- **File Claims**: File insurance claims (collision, theft, comprehensive, glass, vandalism)
- **Risk Analysis**: Automated claim risk assessment
- **Human-in-the-Loop**: SUSPEND/RESUME pattern for high-risk claims requiring human review
- **Claim Resolution**: Auto-approve low-risk claims, manual review for high-risk
- **Notifications**: Send claim resolution notifications to customers

### Customer Service
- **Memory**: Remember customer preferences and history across sessions (optional)
- **Tracing**: OpenTelemetry distributed tracing (optional)
- **Policy Catalogs**: Tool-level configuration for network, timeout, redaction


## Claims Processing Flow

```
User Files Claim → file_claim → analyze_claim_risk → Decision
                                                        │
                    ┌───────────────────────────────────┴───────────────────────────────────┐
                    ↓                                                                       ↓
            Low Risk (<$1K)                                                   Medium/High Risk (≥$5K)
                    ↓                                                                       ↓
            resolve_claim                                                            human_review
            (auto-approve)                                                                  ↓
                    ↓                                                                  SUSPEND
            send_notification                                                               ↓
                    ↓                                                          (Wait for human)
                COMPLETE                                                                    ↓
                                                                                       RESUME
                                                                                           ↓
                                                                                   resolve_claim
                                                                                           ↓
                                                                                send_notification
                                                                                           ↓
                                                                                       COMPLETE
```

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (required for `agentic` CLI)
- LLM API key (Anthropic, Gemini, or OpenAI)

### Setup

```bash
# Create .env file
cp env.example .env
# Edit .env with your API keys
# Required: Set at least one LLM API key (ANTHROPIC_API_KEY, GEMINI_API_KEY, or OPENAI_API_KEY)
# Required by the default agent.yaml: Set VOYAGE_API_KEY for memory, or set features.memory=false
# Note: MONGODB_URI is provided automatically by agentic dev in local mode

# Install dependencies with uv
uv sync
```

### Local Development with `agentic dev`

The `agentic` CLI manages all infrastructure (MongoDB, OE, AER, Tool Pod, Guardrails, Playground UI) via Docker Compose.

#### Hot-Reload Session

Start a development session with hot-reload enabled — your local code changes are reflected immediately without restarting:

```bash
agentic dev up
```

This will:
- Build and start all services (OE, AER, Tool Pod, Guardrails, MongoDB, Playground UI)
- Mount your local source code into the containers
- Watch for file changes and automatically reload

Once running:
- **Playground UI**: http://localhost:3000
- **OE (Orchestrator)**: http://localhost:8000
- **AER**: http://localhost:8001
- **Tool Pod**: http://localhost:8002

Test with:

```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{"message":"I need a quote for auto insurance","user_id":"user-1","thread_id":"t1"}'
```

#### Isolated Session

Start a fully isolated session that builds a fresh container image from your code — useful for testing the exact image that would be deployed:

```bash
agentic dev up --isolated
```

This will:
- Build a self-contained Docker image with your code baked in (no volume mounts)
- Start all services using the built image
- Provide a clean, reproducible environment matching production

> **Tip:** Use `--isolated` for final validation before pushing. Use the default hot-reload mode for day-to-day development.

#### Stopping the Session

```bash
agentic dev down
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MONGODB_URI` | MongoDB connection string (provided automatically by `agentic dev`; set manually for Atlas) | - |
| `MONGODB_DATABASE` | Database name for policies/claims | `insurance_agent` |
| `MDB_AGENTIC_STORE_DB` | Database name for runtime checkpoints, memory, and traces | `mdb_agentic_store_insurance_agent` |
| `ORG_ID` | Organization ID for multi-tenant isolation | `123456789012345678901234` |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `ANTHROPIC_BASE_URL` | Optional Anthropic-compatible API base URL, including Grove Foundry routes | - |
| `GEMINI_API_KEY` | Google Gemini API key | - |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `OPENAI_BASE_URL` | Optional OpenAI-compatible API base URL, including Grove Foundry routes | - |
| `VOYAGE_API_KEY` | Voyage AI API key, required when `features.memory: true` in `agent.yaml` | - |

Feature flags such as memory and guardrails are configured in `agent.yaml`.

### Example Requests

**Get a Quote:**
```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I need a quote for a 2022 Toyota Camry with comprehensive coverage. I am 35 years old.",
    "user_id": "customer-001",
    "thread_id": "conv-001"
  }'
```

**File a Claim:**
```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I need to file a claim. My car was in a collision. Policy is POL-123, damage is about $2,000.",
    "user_id": "customer-001",
    "thread_id": "conv-002"
  }'
```

**Resume After Human Review:**
```bash
curl -X POST http://localhost:8000/resume/{execution_id} \
  -H "Content-Type: application/json" \
  -d '{
    "human_review": {
      "decision": "approved",
      "reviewer_notes": "Verified documentation. Approved for full amount."
    }
  }'
```

## Tools

### Policy Management Tools

| Tool | Description | Execution |
|------|-------------|-----------|
| `lookup_policy` | Look up policy by number | Remote |
| `get_quote` | Generate personalized insurance quote | Remote |
| `create_policy` | Create a new insurance policy | Remote |
| `list_customer_policies` | List all policies for customer | Remote |

### Claims Tools

| Tool | Description | Execution |
|------|-------------|-----------|
| `file_claim` | File a new insurance claim | Remote |
| `analyze_claim_risk` | Analyze claim for risk assessment | Remote (with policy catalog) |
| `human_review` | Request human review (triggers SUSPEND) | Local |
| `resolve_claim` | Resolve a claim after review | Remote |
| `check_claim_status` | Check current claim status | Remote |
| `list_customer_claims` | List all claims for customer | Remote |
| `send_notification` | Send resolution notification (email redacted) | Local |

### Memory Tools

| Tool | Description | Execution |
|------|-------------|-----------|
| `save_customer_info` | Save customer details to memory | Remote |
| `recall_customer_info` | Recall stored customer info | Remote |
| `save_conversation_summary` | Save conversation summary | Remote |
| `recall_past_conversations` | Search past conversations | Remote |
| `explain_insurance_term` | Look up insurance terminology | Remote |
| `get_coverage_options` | Get coverage level information | Remote |

## Risk Assessment Guidelines

The agent uses these guidelines to determine claim handling:

| Condition | Action |
|-----------|--------|
| Claim < $1,000 AND low risk | Auto-approve |
| Claim $1,000-$5,000 OR medium risk | Human review recommended |
| Claim > $5,000 OR high risk | Human review required |
| Claim exceeds coverage limit | Human review required |
| Multiple previous claims | Human review recommended |
| Risk score ≥ 0.6 | Human review required |

## Sample Conversations

### Customer Acquisition Flow

```
User: Hi! I'm John Smith, email john@example.com. I need auto insurance.
Agent: [Saves customer info, asks about vehicle]

User: I have a 2022 Toyota Camry SE. I'm 35 and want comprehensive coverage.
Agent: [Generates quote with pricing details]

User: That looks good, please create the policy.
Agent: [Creates policy POL-XXX, provides policy number]
```

### Claims Flow (Auto-Approve)

```
User: I need to file a claim. My car got a small dent, about $500 damage.
Agent: [Files claim, analyzes risk as LOW, auto-approves, sends notification]
```

### Claims Flow (Human Review)

```
User: My car was stolen. It's worth about $15,000.
Agent: [Files claim, analyzes risk as HIGH, triggers human_review]
       → Execution SUSPENDS, waiting for human decision

[Human reviewer approves via /resume endpoint]

Agent: [Resolves claim as approved, sends notification to customer]
```

## 10-Persona Local Eval

For the full transcript-based eval, use the dedicated local OE runner:

```bash
cd eval
npx tsx insurance-agent-flow.ts --transport both
```

The script:

- Auto-discovers a healthy local app URL from the live local stack and falls back to `.agentic/dev-state.json`
- Talks directly to the local OE, so no API Gateway auth is required
- Exercises both `/invoke` and `/invoke/stream` by default
- Uses async invoke plus polling for the non-streaming path, and direct SSE for the streaming path
- Writes transcripts to `eval/transcripts/` for grading

Useful options:

```bash
# Run only the streaming transport
npx tsx insurance-agent-flow.ts --transport stream

# Run only the async invoke + poll transport
npx tsx insurance-agent-flow.ts --transport invoke

# Run a subset of personas
npx tsx insurance-agent-flow.ts --transport both --personas 1,3,5

# Keep local load low
npx tsx insurance-agent-flow.ts --transport both --concurrency 1

# Override the discovered OE URL
npx tsx insurance-agent-flow.ts --transport both --base-url http://localhost:32825
```

## Logging

All tool and LLM executions are logged to:
1. `logs/executions.jsonl` (JSONL file)
2. MongoDB `execution_logs` collection (when connected)

Each log entry includes:
- Tool name and inputs (with redaction for sensitive fields like `recipient_email`)
- Execution status and duration
- Session/user/thread context
- Risk assessment details for claims

## Directory Structure

```
insurance-agent/
├── src/insurance_agent/
│   ├── __init__.py
│   ├── main.py          # Agent with all tools
│   └── policy_store.py  # Policy and Claim models with MongoDB storage
├── eval/
│   ├── insurance-agent-flow.ts  # Local 10-persona transcript runner
│   └── transcripts/             # Generated eval transcripts
├── pyproject.toml       # Dependencies
├── env.example          # Environment template
├── docker-compose.yml   # Auto-generated by agentic CLI
├── logs/                # Log files
└── README.md
```
