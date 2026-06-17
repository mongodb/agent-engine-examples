# Simple Agent — Web Search Assistant

## What It Does

Helps users search the web and get summarized results via:

- **web_search** — searches DuckDuckGo, crawls the top results with `crawl4ai`, and returns relevant content

## File Structure

```
agents/simple-agent-example/
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
├── agent.yaml                # Agent descriptor for 
├── env.example               # Environment variable template
├── Dockerfile                # Production container
├── Makefile                  # Development commands
└── README.md                 # This file
```


## Setup

```bash
cd agents/simple-agent-example

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

The agent uses the ATLASAP SDK with a standard LangGraph pattern:

```
START → agent → should_continue? → tools → agent → ... → END
```

- **agent node** — invokes the LLM with system prompt + conversation history
- **tools node** — executes tool calls from the LLM response
- **should_continue** — routes to tools or END, with a safety limit of 10 tool calls per turn


**`.env` setup** —  LLM URLs and Keys must be added to the env file. 
