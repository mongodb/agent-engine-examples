# Simple Agent — Web Search Assistant

A reference agent migrated from `mdb_agents` (Bedrock AgentCore) to `magenta_sdklanggraph`.

## What It Does

Helps users search the web and get summarized results via:

- **web_search** — searches DuckDuckGo, crawls the top results with `crawl4ai`, and returns relevant content

## File Structure

```
agents/simple-agent/
├── src/simple_agent/
│   ├── __init__.py           # Package docstring
│   ├── main.py               # App + @app.entrypoint graph definition
│   ├── state.py              # SimpleAgentState TypedDict
│   ├── system_message.py     # System prompt template
│   ├── tools.py              # Tool definitions (register pattern)
│   └── llm.py                # Multi-provider LLM factory
├── tests/
│   └── test_graph.py         # Graph compilation and execution tests
├── pyproject.toml            # Dependencies & build config
├── agent.yaml                # Agent descriptor for magenta
├── dev.yaml                  # Local-dev service ports (playground pinned to 3000)
├── env.example               # Environment variable template
├── Dockerfile                # Production container
├── Makefile                  # Development commands
└── README.md                 # This file
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- One LLM API key (OpenAI, Anthropic, Gemini, or Cerebras)
- MongoDB instance (for checkpointing)

## Setup

```bash
cd agents/simple-agent

# Create .env from template
cp env.example .env
# Edit .env — set MONGODB_URI and at least one LLM API key

# Install dependencies
make install
```

## Running Locally

```bash
make serve
```

## Testing

```bash
make test          # Run tests
make test-verbose  # Verbose output
make cov           # Tests with coverage
make ci-tests      # Full CI pipeline (install + lint + cov)
```

## Docker

```bash
make docker-build
make docker-run
```

## Architecture

The agent uses the `magenta_sdklanggraph` SDK with a standard LangGraph pattern:

```
START → agent → should_continue? → tools → agent → ... → END
```

- **agent node** — invokes the LLM with system prompt + conversation history
- **tools node** — executes tool calls from the LLM response
- **should_continue** — routes to tools or END, with a safety limit of 10 tool calls per turn

### Azure OpenAI Support (`patch_runtime_llm`)

The platform architecture splits execution across the Orchestration Engine (OE),
AER (graph execution), and Tool Pod (tool + LLM execution). LLM calls from the
graph don't run locally — `SecureWrappedLLM` serializes them and proxies to the
Tool Pod's `/invoke_llm` endpoint, which creates its own LLM via the runtime's
built-in `_create_llm()`.

The problem: `_create_llm()` uses plain `ChatOpenAI` for all OpenAI keys. When
`OPENAI_BASE_URL` points to Azure, it constructs the correct path but **never
adds the `?api-version=` query parameter** that Azure requires, resulting in a
404 error.

`patch_runtime_llm()` (in `llm.py`) fixes this by monkey-patching the runtime's
`_create_llm` to use our `build_llm()`, which adds `default_query={"api-version": ...}`
for Azure endpoints. It is called at module load time in `main.py`.

**When is this needed?** Only when using Azure OpenAI (i.e. `OPENAI_BASE_URL`
contains `.openai.azure.com`). For direct OpenAI, Anthropic, Gemini, or Cerebras
endpoints the patch is a no-op — `build_llm()` behaves identically to the
runtime's built-in factory for those providers.

**Azure `.env` setup** — `OPENAI_BASE_URL` must include the full deployment path:

```bash
OPENAI_BASE_URL="https://<resource>.openai.azure.com/openai/deployments/<deployment>"
OPENAI_API_KEY="<your-azure-api-key>"
OPENAI_MODEL="<deployment-name>"
AZURE_OPENAI_API_VERSION="2024-12-01-preview"  # optional, defaults to this value
```

**Grove Foundry `.env` setup** - set the provider-specific key with the matching
base URL.

```bash
# OpenAI-compatible models
OPENAI_API_KEY="<your-openai-compatible-key>"
OPENAI_BASE_URL="https://grove-gateway-prod.azure-api.net/grove-foundry-prod/openai/v1"
OPENAI_MODEL="gpt-5.4-mini"

# Anthropic-compatible models
ANTHROPIC_API_KEY="<your-anthropic-compatible-key>"
ANTHROPIC_BASE_URL="https://grove-gateway-prod.azure-api.net/grove-foundry-prod/anthropic/v1"
ANTHROPIC_MODEL="claude-sonnet-4-6"
```
