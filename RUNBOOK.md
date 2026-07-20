# Magenta Examples — Contributor Runbook

This runbook guides teams outside of Magenta to create, modify, and contribute agents to the magenta-examples repository.

## Table of Contents

1. [What is Magenta Examples?](#what-is-magenta-examples)
2. [Architecture Overview](#architecture-overview)
3. [Prerequisites](#prerequisites)
4. [Initial Setup](#initial-setup)
5. [Understanding the Project Structure](#understanding-the-project-structure)
6. [Creating a New Agent](#creating-a-new-agent)
7. [Modifying Existing Agents](#modifying-existing-agents)
8. [Testing Your Agent](#testing-your-agent)
9. [Building and Deploying](#building-and-deploying)
10. [Common Patterns and Best Practices](#common-patterns-and-best-practices)
11. [Contribution Workflow](#contribution-workflow)
12. [Troubleshooting](#troubleshooting)

---

## What is Magenta Examples?

**Magenta Examples** is a repository of standalone, self-contained example agents built on the [Magenta Client Libraries](https://github.com/10gen/magenta-client-libraries) SDK. It demonstrates best practices for building intelligent agents using LLMs with tool integration, state management, and deployment patterns.

### Key Features

- **Standalone Agents**: Each agent is a self-contained application with its own dependencies, tests, and deployment metadata. You can copy any agent and run it independently.
- **Multiple Frameworks**: Examples use LangGraph, with support for multi-agent patterns and specialized agent types (Admin, Code Review, Data Analysis, etc.).
- **Templates**: Ready-to-use starter scaffolds for common use cases (chat clients, simple agents, insurance assistants).
- **Workspace Managed**: Uses `uv` (Python package manager) for workspace management, allowing agents to share tooling while remaining deployable individually.
- **Production Ready**: Includes Dockerfiles, CI integration, and deployment metadata for the MongoDB Agentic Platform.

### Example Agents

| Agent | Description | Use Case |
|-------|-------------|----------|
| **simple-agent** | Web search assistant | Learning, basic tool integration |
| **atlas-admin-agent** | MongoDB Atlas admin API with human approval | Admin workflows with review gates |
| **code-reviewer-agent** | DeepAgent code-review orchestrator | Multi-agent patterns, specialist skills |
| **data-analyst-agent** | MongoDB query and chart generation | Data analysis, artifact creation |
| **recruiting-assistant-agent** | Candidate management with memory | Long-context workflows, memory usage |
| **insurance-agent** | Insurance quoting and policy management | Complex state, business logic |
| **weather-agent** | LangGraph integration demo | Framework basics |

---

## Architecture Overview

### Workspace Structure

```
magenta-examples/
├── agent.yaml                 # Root monorepo manifest listing all deployable agents
├── pyproject.toml             # Root workspace metadata
├── uv.toml                    # uv workspace configuration
├── scripts/                   # Utilities (setup wizard, CI helpers)
├── agents/
│   ├── simple-agent/          # Each agent is self-contained
│   │   ├── src/simple_agent/
│   │   │   ├── main.py        # LangGraph app + entry point
│   │   │   ├── state.py       # TypedDict state schema
│   │   │   ├── tools.py       # Tool definitions
│   │   │   ├── system_message.py
│   │   │   └── llm.py         # LLM factory
│   │   ├── tests/
│   │   ├── agent.yaml         # Agent descriptor
│   │   ├── dev.yaml           # Local development config
│   │   ├── pyproject.toml     # Agent dependencies
│   │   ├── Dockerfile         # Production image
│   │   ├── Makefile           # Common tasks
│   │   └── README.md
│   ├── code-reviewer-agent/
│   └── [15+ other agents]
└── templates/
    ├── hello-world-agent/     # Starter template
    ├── hello-world-agent-langgraph-ts/
    ├── insurance-agent/       # Insurance template
    └── chatbot-client/        # Next.js chat UI

```

### Agent Structure

Every agent follows this pattern:

```python
# main.py
from langgraph.graph import StateGraph
from langgraph.prebuilt import create_react_agent

@app.entrypoint
def main(state: AgentState):
    """Entry point for the agent."""
    return create_react_agent(
        llm=llm,
        tools=tools,
        state_modifier=state_modifier,
        ...
    )
```

**Key Components**:
- **State**: A `TypedDict` defining the agent's input/output schema
- **Tools**: Callable functions registered as agent tools
- **LLM**: Language model client (supports OpenAI, Anthropic, Gemini, Cerebras)
- **Graph**: LangGraph workflow (START → agent → tools → END)

---

## Prerequisites

Before you start, ensure you have:

1. **Python 3.11 or higher**
   ```bash
   python --version  # Should show 3.11+
   ```

2. **uv Package Manager**
   ```bash
   # Install uv (https://docs.astral.sh/uv/)
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Verify installation
   uv --version
   ```

3. **Git**
   ```bash
   git --version
   ```

4. **Docker** (optional, for containerized deployment)
   ```bash
   docker --version
   ```

5. **LLM API Key** (at least one of):
   - OpenAI API Key (`OPENAI_API_KEY`)
   - Anthropic API Key (`ANTHROPIC_API_KEY`)
   - Google Gemini API Key (`GOOGLE_API_KEY`)
   - Cerebras API Key (`CEREBRAS_API_KEY`)

6. **MongoDB Instance** (for checkpointing and memory)
   - Local MongoDB server, or
   - MongoDB Atlas connection string

---

## Initial Setup

### 1. Clone the Repository

```bash
git clone https://github.com/10gen/magenta-examples.git
cd magenta-examples
```

### 2. Initialize the Workspace

```bash
# Sync dependencies for all agents in the workspace
uv sync
```

This installs:
- The Magenta SDK (`magenta_sdklanggraph`)
- Shared dependencies (langgraph, pydantic, etc.)
- Dependencies for all agents

### 3. Verify Setup

```bash
# Test that uv works
uv --version

# Check a simple agent
cd agents/simple-agent
uv run --help
```

---

## Understanding the Project Structure

### Root Files

| File | Purpose |
|------|---------|
| `agent.yaml` | Manifest of all deployable agents in the workspace |
| `pyproject.toml` | Root workspace metadata and shared build settings |
| `uv.toml` | uv workspace configuration |
| `.agenticignore` | Archive filter for `agentic build` (like `.gitignore`) |
| `scripts/setup-agent` | Interactive wizard to set up a demo locally |

> **Note**: `agent.yaml`'s agent list and `pyproject.toml`'s `[tool.uv.workspace] members` list are related but not identical — a package can be a `uv` workspace member (built/synced locally) without being listed in `agent.yaml` (deployed to the Agentic Platform), e.g. an agent still under internal development. Don't assume the two lists are interchangeable.

### Agent Files

Each agent directory (e.g., `agents/simple-agent/`) contains:

| File | Purpose |
|------|---------|
| `agent.yaml` | Agent descriptor (name, framework, entry point, required secrets) |
| `dev.yaml` | Local-dev configuration (service ports, etc.) |
| `pyproject.toml` | Dependencies, build config, scripts |
| `src/agent_name/` | Python package with agent code |
| `tests/` | Test suite |
| `Dockerfile` | Production image definition |
| `Makefile` | Common tasks (`make serve`, `make test`, `make docker-build`) |
| `env.example` | Template for environment variables |
| `README.md` | Agent-specific documentation |

### Templates

Templates are starter scaffolds designed to be copied for new projects:

```bash
cp -r templates/hello-world-agent my-new-agent
cd my-new-agent
uv sync
```

---

## Creating a New Agent

### Method 1: From a Template (Recommended)

Templates are the easiest way to bootstrap a new agent.

#### Step 1: Copy a Template

```bash
# Choose a template that matches your use case
# - hello-world-agent: Minimal agent with tools
# - hello-world-agent-langgraph-ts: TypeScript variant
# - insurance-agent: Complex state and business logic

cp -r templates/hello-world-agent agents/my-custom-agent
cd agents/my-custom-agent
```

#### Step 2: Update Metadata

Edit `agent.yaml`:
```yaml
name: my-custom-agent
entrypoint: my_custom_agent.main:app
framework: langgraph

required_secrets:
  aer:
    - "<YOUR LLM API KEY>"
  tools:
    invoke_llm:
      - "<YOUR LLM API KEY>"
```

Edit `pyproject.toml`:
```toml
[project]
name = "my-custom-agent"
version = "0.1.0"
description = "My custom agent description"
requires-python = ">=3.11"

[dependency-groups]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.1",
]
```

Rename the package:
```bash
mv src/hello_world_agent src/my_custom_agent
```

Edit `src/my_custom_agent/__init__.py` to reflect the new name.

The `mv` and the two `sed` commands above only cover the directory and the two metadata fields — they do **not** rewrite every reference to the old package name. Do a repo-wide rename to catch the rest:

```bash
grep -rl "hello_world_agent" . --include="*.py" --include="*.toml" --include="*.yaml" | \
  xargs sed -i '' 's/hello_world_agent/my_custom_agent/g'

grep -rl "hello-world-agent" . --include="*.py" --include="*.toml" --include="*.yaml" | \
  xargs sed -i '' 's/hello-world-agent/my-custom-agent/g'
```

This fixes, at minimum:
- `src/my_custom_agent/main.py` — `from hello_world_agent.* import ...`
- `src/my_custom_agent/tools.py` — `source="hello_world_agent"`
- `agent.yaml` — `entrypoint: hello_world_agent.main:app`
- `pyproject.toml` — `[project.scripts]` (`hello-world-agent = "hello_world_agent.main:main"`) and `[tool.hatch.build.targets.wheel] packages = ["src/hello_world_agent"]`

After renaming, verify no old references remain:
```bash
grep -rn "hello_world_agent\|hello-world-agent" . && echo "still references old name" || echo "clean"
```

#### Step 3: Install Dependencies

```bash
uv sync
```

#### Step 4: Customize Your Agent Logic

The template includes a complete agent structure with all necessary files already created. Customize these existing files to fit your use case:

**Files already included in template:**
- `main.py` — Agent graph definition
- `state.py` — State schema
- `tools.py` — Tool definitions
- `system_message.py` — System prompt
- `llm.py` — LLM configuration
- `pyproject.toml` — Dependencies and metadata
- `agent.yaml` — Agent descriptor

**Files you may need to add:**
- `Makefile` — Common development tasks (test, serve, lint, etc.)
- `Dockerfile` — Production container definition
- `tests/` directory with test files

**Customize `src/my_custom_agent/main.py`** (already exists):

The template's `main.py` already has a working LangGraph structure. Modify only if you need custom nodes:

```python
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from magenta_sdklanggraph import App

from my_custom_agent.llm import build_llm
from my_custom_agent.state import MyCustomAgentState
from my_custom_agent.system_message import SYSTEM_PROMPT
from my_custom_agent.tools import register

app = App(app_name="my-custom-agent")
register(app)

@app.entrypoint
def build_agent():
    """Build the LangGraph agent."""
    llm_with_tools = app.llm(build_llm()).bind_tools(app.get_tool_schemas())
    tools = app.get_tools()
    
    def agent_node(state: MyCustomAgentState):
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
        else:
            prompt_messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages[1:])
        
        response = llm_with_tools.invoke(prompt_messages)
        response = app.validate_llm_response(response)
        return {"messages": [response]}
    
    def should_continue(state: MyCustomAgentState):
        last = state["messages"][-1]
        return "tools" if hasattr(last, "tool_calls") and last.tool_calls else "end"
    
    builder = StateGraph(MyCustomAgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())
```

**Customize `src/my_custom_agent/tools.py`** (already exists with example tools):

```python
from magenta_sdklanggraph import App

def register(app: App) -> None:
    """Register tools with the app."""
    
    @app.tool(is_local=False)
    def fetch_data(query: str) -> str:
        """Fetch data based on query.
        
        Args:
            query: The search query
        
        Returns:
            The data matching the query
        """
        # Your implementation here
        return f"Data for: {query}"
    
    @app.tool(is_local=False)
    def get_user_profile(user_id: str) -> dict:
        """Get user profile information.
        
        Args:
            user_id: The user's ID
        
        Returns:
            User profile data
        """
        return {"id": user_id, "name": "User Name"}
```

**Customize `src/my_custom_agent/system_message.py`** (already exists):

```python
SYSTEM_PROMPT = """You are a helpful assistant specializing in X.

When the user asks for Y, use the fetch_data tool.
Always be accurate and helpful.
"""
```

**Customize `src/my_custom_agent/state.py`** (already exists):

If you need custom state fields beyond the default, update the TypedDict:

```python
from typing import TypedDict
from langchain_core.messages import AnyMessage

class MyCustomAgentState(TypedDict):
    """Agent state schema."""
    messages: list[AnyMessage]
    user_id: str  # Add your custom fields
    search_results: dict | None
```

**Customize `src/my_custom_agent/llm.py`** (already exists):

The template includes multi-provider LLM support. Modify if you need custom model configuration.

#### Step 5: Create a Makefile (Optional but Recommended)

Templates don't include a Makefile, but you'll need one for `make test`, `make serve`, etc. Copy from an existing agent or create one:

```bash
# Option 1: Copy from simple-agent
cp ../simple-agent/Makefile .

# Then update the agent name in the Makefile:
sed -i '' 's/simple_agent/my_custom_agent/g' Makefile
sed -i '' 's/simple-agent/my-custom-agent/g' Makefile
```

Or create your own `Makefile`:

```makefile
SOURCE_DIRS := src tests
RUN := uv run
PYRUN := $(RUN) python
RUFF_CMD := $(RUN) ruff
PYRIGHT_CMD := $(RUN) pyright
RUN_COV := $(RUN) pytest --cov=my_custom_agent --cov-report term-missing --cov-report html tests/

.PHONY: serve install cov test test-verbose lint format ci-tests clean docker-build docker-run help

install:
	uv sync --dev

test:
	$(RUN) pytest tests/

test-verbose:
	$(RUN) pytest tests/ -vv

cov:
	$(RUN_COV)

lint:
	$(RUFF_CMD) check $(SOURCE_DIRS)
	$(RUFF_CMD) format --check $(SOURCE_DIRS)
	$(PYRIGHT_CMD)

format:
	$(RUFF_CMD) format $(SOURCE_DIRS)
	$(RUFF_CMD) check $(SOURCE_DIRS) --fix

ci-tests: install lint cov

serve: install
	$(PYRUN) -m my_custom_agent.main

docker-build:
	docker build -t my-custom-agent .

docker-run:
	docker run -p 8001:8001 --env-file .env my-custom-agent

clean:
	rm -rf .pytest_cache/ htmlcov/ __pycache__/
	find . -name "*.pyc" -delete
	find . -name "*.pyo" -delete

help:
	@echo "Available targets:"
	@echo "  make install      - Install dependencies"
	@echo "  make test         - Run tests"
	@echo "  make cov          - Run with coverage"
	@echo "  make serve        - Run the agent locally"
	@echo "  make docker-build - Build Docker image"
```

#### Step 6: Set Up Environment

```bash
cp env.example .env
# Edit .env with your API keys and MongoDB URI
```

### Method 2: From an Existing Agent

If you want to extend or modify an existing agent:

```bash
cp -r agents/simple-agent agents/my-fork-agent
cd agents/my-fork-agent

# Rename package directory
mv src/simple_agent src/my_fork_agent

# Before copying, clean the source agent's runtime artifacts so they don't
# get carried into the fork (logs/, coverage, and .venv are not renaming
# targets — they're just stale clutter that's confusing in a freshly named dir):
#   rm -rf logs/ observability/ .venv/ .coverage htmlcov/  (run in agents/simple-agent first)
#
# Rename every reference to the old package name (hyphenated and underscored),
# across all source, config, and build files — not just agent.yaml/pyproject.toml.
# Both passes must include Makefile and Dockerfile: the underscored name shows up
# in Dockerfile's CMD and the Makefile's cov/serve targets, not just the hyphenated
# name. `uv sync` will succeed even if this step is skipped or incomplete (it
# doesn't validate that imports resolve), so you won't find out until you run
# `make cov` or `make docker-build`.
grep -rl "simple_agent" . --include="*.py" --include="*.toml" --include="*.yaml" \
  --include="*.md" --include="Makefile" --include="Dockerfile" \
  --include="env.example" --include="*.json" --include="*.yml" | \
  xargs sed -i '' 's/simple_agent/my_fork_agent/g'

grep -rl "simple-agent" . --include="*.py" --include="*.toml" --include="*.yaml" \
  --include="*.md" --include="Makefile" --include="Dockerfile" \
  --include="env.example" --include="*.json" --include="*.yml" | \
  xargs sed -i '' 's/simple-agent/my-fork-agent/g'

uv sync
```

This must touch, at minimum:
- `pyproject.toml` — `[project] name = "simple-agent"` (hyphenated — a `simple_agent` sed pattern alone will miss this)
- `src/my_fork_agent/main.py` / `llm.py` — imports and `app_name="simple-agent"`
- `Makefile` and `Dockerfile` — both reference the old module/image name in *both* underscored and hyphenated forms (e.g. `RUN_COV := ... --cov=simple_agent`, `CMD ["python", "-m", "simple_agent.main"]`) — easy to miss because `make test` still passes even when these are stale, since `pytest tests/` doesn't touch the Dockerfile/Makefile strings
- `tests/` — test imports (e.g. `from simple_agent.main import app`)
- `README.md` and `env.example` — not covered by `.py`/`.toml`/`.yaml` globs alone

Verify no old references remain before running tests — this should print "clean":
```bash
grep -rn "simple_agent\|simple-agent" . && echo "still references old name" || echo "clean"
```

---

## Modifying Existing Agents

### Adding a New Tool

Tools are registered in `src/agent_name/tools.py` using the `@app.tool()` decorator:

```python
from magenta_sdklanggraph import App

def register(app: App) -> None:
    """Register all tools with the app."""
    
    @app.tool(is_local=False)
    def search_database(query: str, limit: int = 10) -> dict:
        """Search the database for matching records.
        
        Args:
            query: The search query
            limit: Maximum number of results (default 10)
        
        Returns:
            Dictionary with results and metadata
        """
        # Your implementation
        results = [{"id": 1, "name": "Result 1"}]
        return {"status": "success", "results": results, "count": len(results)}

    @app.tool(is_local=False)
    def get_user_profile(user_id: str) -> dict:
        """Fetch user profile information.
        
        Args:
            user_id: The user's unique identifier
        
        Returns:
            User profile data
        """
        # Implementation
        return {"id": user_id, "name": "User Name", "status": "active"}
```

Then in `main.py`, the tools are automatically available via `app.get_tools()` once registered.

### Modifying State Schema

Edit `src/agent_name/state.py`:

```python
from typing import TypedDict, Optional
from langgraph.graph.message import AnyMessage

class AgentState(TypedDict):
    """Agent state schema."""
    messages: list[AnyMessage]
    user_id: str
    context: Optional[dict]
    search_results: Optional[list[dict]]
```

### Updating System Prompt

Edit `src/agent_name/system_message.py`:

```python
SYSTEM_PROMPT = """You are a helpful assistant specializing in X.

When the user asks for Y, use the Z tool.

Always consider:
- Factor A
- Factor B

Respond in JSON format when appropriate.
"""
```

### Adding Custom Logic

For complex agents, add custom nodes to the graph in `main.py`:

```python
@app.entrypoint
def main():
    graph_builder = StateGraph(AgentState)
    
    llm = build_llm()
    tools = get_tools()
    
    # Add agent node
    graph_builder.add_node("agent", call_llm)
    
    # Add custom processing node
    graph_builder.add_node("process", process_results)
    
    # Connect nodes
    graph_builder.add_edge("agent", "should_continue")
    graph_builder.add_conditional_edges("should_continue", ...)
    
    return graph_builder.compile()
```

---

## Testing Your Agent

### Prerequisites: Add Test Dependencies

Templates don't include pytest by default. Add to `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "pyright>=1.1.390",
    "ruff>=0.1.0",
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.1",
    "pytest-cov>=4.0.0",
]
```

Then sync:
```bash
uv sync --dev
```

### Create conftest.py for Test Environment

Create `tests/conftest.py` to set up environment variables:

```python
"""Pytest configuration for your-agent."""

import os
import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up test environment variables."""
    # Set RUNNER_MODE (required by Magenta SDK)
    os.environ.setdefault("RUNNER_MODE", "aer")
    
    # Set a dummy LLM key for testing
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-key-for-testing")
    os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")
```

### Running Tests

```bash
cd agents/my-agent

# Run all tests
make test

# Run with verbose output
make test-verbose

# Run with coverage report
make cov

# Full CI pipeline (install, lint, coverage, tests)
make ci-tests
```

> **Note**: `make cov`/`make ci-tests` invoke `pytest --cov=...`, which requires `pytest-cov`. It is not declared in `simple-agent`'s (or the templates') dev dependency group by default — add `"pytest-cov>=4.0.0"` to `[dependency-groups] dev` in `pyproject.toml` (see the dev-dependency snippet above) and run `uv sync --dev` before `make cov` will work.

### Writing Tests

Create test files in `tests/`:

```python
# tests/test_graph.py
"""Test the agent graph compilation."""

import os

# Set RUNNER_MODE before importing the app
os.environ.setdefault("RUNNER_MODE", "aer")

import pytest

from my_agent.main import build_agent


@pytest.fixture(scope="module")
def agent():
    """Build the graph once per test module.

    The Magenta SDK's app.llm() registers the LLM under a unique llm_id
    the first time it's called; calling build_agent() again in the same
    process raises `ValueError: llm_id '__default__' is already registered`.
    Share one build across tests via this fixture instead of calling
    build_agent() in every test function.
    """
    return build_agent()


def test_graph_compiles(agent):
    """Test that the agent graph compiles successfully."""
    assert agent is not None
    assert hasattr(agent, "invoke")


def test_graph_has_nodes(agent):
    """Test that the graph has expected nodes."""
    # Access the graph structure
    assert agent is not None
```

```python
# tests/test_tools.py
"""Test agent tools."""

import os
import pytest

os.environ.setdefault("RUNNER_MODE", "aer")

from my_agent.main import app


def test_tools_registered():
    """Test that tools are properly registered."""
    tools = app.get_tools()
    assert len(tools) > 0
    # Check that expected tool names are present
    tool_names = [tool.name for tool in tools]
    assert "get_current_date" in tool_names
```

### Running Locally

```bash
# Start the agent in development mode
make serve

# Or with explicit uv
uv run --detached agentic dev up
```

This starts:
- The agent server (typically on `localhost:8000`)
- A web playground (typically on `localhost:3000`)

You can then:
- Test via the web UI at `localhost:3000`
- Send requests to the API: `curl http://localhost:8000/invoke`
- View logs in the terminal

---

## Building and Deploying

Use the `agentic` CLI to package and deploy agents. You should not need to build or run Docker images manually for normal workflows.

### Archive for Deployment

The `agentic` CLI packages agents for deployment:

```bash
cd agents/my-agent

# One-time prerequisite: register/authenticate this workspace with the
# Agentic Platform. Without this, `agentic build` fails with
# "Error: no workspace registered: run 'agentic init'".
agentic auth login
agentic init

# Build the deployment package
agentic build

# This creates a source archive respecting .agenticignore
# Upload the archive to your deployment environment
```

If a Docker-based build fails while resolving private deps from `10gen/magenta-client-libraries` with:

```
fatal: could not read Username for 'https://github.com': terminal prompts disabled
```

authenticate git for the build environment (for example, forward an SSH agent with `docker build --ssh default`, or pass a GitHub token/netrc as a build secret). Confirm credentials work outside Docker with `git ls-remote https://github.com/10gen/magenta-client-libraries`.

### Environment Variables

Before deployment, ensure all required variables are set:

```bash
# .env file
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/db
OPENAI_API_KEY=sk-...
# or ANTHROPIC_API_KEY, GOOGLE_API_KEY, CEREBRAS_API_KEY

# Optional
OPENAI_BASE_URL=...  # For Azure or compatible endpoints
OPENAI_MODEL=gpt-4o
DEBUG=true
```

Note: the shipped `env.example` files (templates and existing agents) don't include `OPENAI_MODEL` or `DEBUG` — they're optional overrides with sane defaults in `llm.py`. Add them to your `.env` yourself if you need to override the model or enable debug logging.

The `.agenticignore` file controls what gets archived. By default, `.env` files are excluded (never committed/deployed).

---

## Common Patterns and Best Practices

### 1. Tool Definition Pattern

Tools in magenta-examples use the `@app.tool()` decorator pattern:

```python
from magenta_sdklanggraph import App

def register(app: App) -> None:
    """Register all tools with the app."""
    
    @app.tool(is_local=False)
    def my_tool(param1: str, param2: int = 10) -> dict:
        """Clear, concise description for the LLM.
        
        The docstring becomes the tool description. Include parameter
        types and what the tool does.
        
        Args:
            param1: First parameter description
            param2: Second parameter (optional, default 10)
        
        Returns:
            Dictionary with results and status
        """
        return {"status": "success", "data": [], "count": 0}
    
    @app.tool(is_local=False)
    def another_tool(query: str) -> str:
        """Search for information."""
        return "Result from search"
```

Then call `register(app)` in `main.py` at module level, and use `app.get_tools()` and `app.get_tool_schemas()` to access them.

### 2. Multi-Provider LLM Support

Use a factory function in `llm.py`:

```python
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

def build_llm():
    """Build LLM from environment variables."""
    if OPENAI_API_KEY:
        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            api_key=OPENAI_API_KEY,
        )
    elif ANTHROPIC_API_KEY:
        return ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            api_key=ANTHROPIC_API_KEY,
        )
    else:
        raise ValueError("No LLM API key configured")
```

### 3. State Management

Keep state schema focused on what the graph needs:

```python
class AgentState(TypedDict):
    """Minimal, clear state."""
    messages: list[AnyMessage]  # Conversation history
    user_context: dict          # User-specific data
    tools_called: int           # Counter for safety limits
```

### 4. Error Handling in Tools

```python
@tool
def risky_operation(input_data: str) -> dict:
    """A tool that might fail."""
    try:
        # Risky operation
        result = external_api_call(input_data)
        return {"status": "success", "data": result}
    except Exception as e:
        # Return error in structured format
        return {
            "status": "error",
            "error": str(e),
            "suggestion": "Please try again or check parameters"
        }
```

### 5. Azure OpenAI Support

When using Azure OpenAI:

```python
# llm.py
import os
from langchain_openai import ChatOpenAI

def build_llm():
    base_url = os.getenv("OPENAI_BASE_URL", "")
    
    if ".openai.azure.com" in base_url:
        # Azure requires api-version query parameter
        return ChatOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            base_url=base_url,
            model=os.getenv("OPENAI_MODEL"),
        )
    else:
        return ChatOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        )
```

### 6. Testing Patterns

```python
# Test graph compilation
def test_graph_compiles():
    compiled = app()
    assert compiled is not None

# Test tool execution
@pytest.mark.asyncio
async def test_tool():
    result = await my_tool("input")
    assert result["status"] == "success"

# Test end-to-end with mocked LLM
@pytest.mark.asyncio
async def test_agent_flow(mock_llm):
    state = AgentState(messages=[...])
    result = await app(state)
    assert len(result["messages"]) > 0
```

---

## Contribution Workflow

### 1. Create a Feature Branch

```bash
git checkout -b feature/my-new-agent

# or for bug fixes
git checkout -b fix/bug-description
```

### 2. Make Your Changes

- Create your agent in `agents/my-agent/`
- Or modify an existing agent
- Add tests
- Update documentation (agent README, examples)

### 3. Test Everything

```bash
cd agents/my-agent

# Run tests
make ci-tests

# Test locally
make serve

# Verify Docker build
make docker-build
```

### 4. Update Root Manifest

If you're adding a new agent, add it to `agent.yaml`:

```yaml
agents:
  - name: my-custom-agent
    path: agents/my-custom-agent
```

### 5. Commit and Create PR

```bash
git add agents/my-agent/
git add agent.yaml  # if adding new agent
git commit -m "Add my-custom-agent: [brief description]"

git push origin feature/my-new-agent
```

On GitHub, create a Pull Request with:
- Clear description of what the agent does
- Why it's useful
- How to test it locally
- Any configuration requirements

### 6. Code Review

Maintainers will review for:
- Code quality and style
- Security (no hardcoded secrets, proper error handling)
- Documentation completeness
- Test coverage
- Compatibility with the Magenta SDK

### Contribution Guidelines

- **Use uv for dependency management** — don't use pip directly
- **Follow the agent structure** — src/, tests/, Makefile, agent.yaml, etc.
- **Write clear docstrings** — they become tool descriptions for LLMs
- **Include tests** — aim for >80% coverage
- **No hardcoded secrets** — use environment variables
- **Update README** — document your agent's purpose and usage
- **Keep agents self-contained** — should work independently

---

## Troubleshooting

### "uv sync" Fails

**Problem**: Dependency resolution errors

**Solution**:
```bash
# Clear cache
rm -rf .venv
uv cache clean

# Retry
uv sync
```

### Missing LLM API Key

**Problem**: Agent fails with "No LLM API key configured"

**Solution**:
```bash
cp env.example .env
# Edit .env with your API key
source .env
make serve
```

### Graph Compilation Error

**Problem**: "StateGraph requires all nodes to have edges"

**Solution**: Ensure your graph has:
- A START node (implicit)
- At least one regular node
- Edges from all nodes
- An END node (or conditional routing to END)

```python
graph_builder = StateGraph(AgentState)
graph_builder.add_node("agent", call_llm)
graph_builder.add_node("tools", execute_tools)

graph_builder.add_edge(START, "agent")
graph_builder.add_edge("agent", "tools")
graph_builder.add_edge("tools", END)
```

### Port Already in Use

**Problem**: "Address already in use" on port 3000/8000

**Solution**:
```bash
# Find process using port
lsof -i :3000

# Stop the conflicting process, e.g.:
kill <PID>
```

Note: `make serve` has no `PORT` variable — `make serve PORT=3001` will run without error but has **no effect** on the bound port (`make serve` binds `8001` by default, not `8000`; see the `Makefile`'s `serve` target). To actually change the port, edit the `serve` target's command or set the port via whatever env var/CLI flag `runner_shared`'s server reads (check `src/<agent>/main.py` / SDK docs), then re-run `make serve`.

Also note: since the app binds `8001` internally, `docker-run`'s port mapping must publish `8001:8001` (not `8000:8000`) or the host port won't actually reach the container — the `docker-run` Makefile target above has been corrected accordingly.

### Docker Build Fails

**Problem**: "Docker build exited with code 1"

**Solution**:
```bash
# Check Dockerfile
cat Dockerfile

# Build with verbose output
docker build -t my-agent:latest . --progress=plain

# Ensure .dockerignore and .agenticignore are correct
```

### Tool Not Found at Runtime

**Problem**: "Tool 'my_tool' not found"

**Solution**:
1. Ensure tool is defined in `tools.py` with the `@app.tool()` decorator
2. Ensure `register(app)` is called in `main.py`
3. Ensure the tool is bound to the LLM via `app.get_tool_schemas()`

```python
# tools.py
def register(app: App) -> None:
    @app.tool(is_local=False)
    def my_tool(param: str) -> dict:
        """Tool description."""
        return {"result": "value"}

# main.py
from my_agent.tools import register

app = App(app_name="my-agent")
register(app)  # Must be called

@app.entrypoint
def build_agent():
    llm_with_tools = app.llm(build_llm()).bind_tools(app.get_tool_schemas())
    tools = app.get_tools()  # Retrieves registered tools
    ...
```

### MongoDB Connection Error

**Problem**: "Failed to connect to MongoDB"

**Solution**:
```bash
# Check connection string
echo $MONGODB_URI

# Test connection
mongosh $MONGODB_URI --eval "db.version()"

# For local MongoDB, ensure it's running
mongod  # or
docker run -d -p 27017:27017 mongo
```

### Agent Response is Empty

**Problem**: Agent returns empty messages or doesn't invoke tools

**Solution**:
- Check system prompt is not too restrictive
- Verify tools are accessible to the LLM
- Enable debug logging: `DEBUG=true make serve`
- Check agent logs for errors

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## Additional Resources

- [Magenta SDK Documentation](https://github.com/10gen/magenta-client-libraries)
- [LangGraph Official Docs](https://langchain-ai.github.io/langgraph/)
- [LangChain Tools](https://python.langchain.com/docs/modules/tools/)
- [MongoDB Documentation](https://docs.mongodb.com)
- [uv Package Manager](https://docs.astral.sh/uv/)

---

## Getting Help

- **GitHub Issues**: [Report bugs](https://github.com/10gen/magenta-examples/issues)
- **Discussions**: [Ask questions](https://github.com/10gen/magenta-examples/discussions)
- **Documentation**: See individual agent READMEs for use-case-specific details
- **Community**: MongoDB AI Forums [link]

---

## Quick Reference: Common Commands

```bash
# Setup
uv sync                    # Install dependencies
cp env.example .env        # Set up environment

# Development
make serve                 # Run agent locally
make test                  # Run tests
make test-verbose          # Verbose test output
make cov                   # Test coverage

# Building
make docker-build          # Build Docker image
make docker-run            # Run Docker container

# Cleanup
make clean                 # Remove build artifacts
rm -rf .venv               # Remove virtual environment
```

---

**Last Updated**: 2026-07-09  
**Version**: 1.0  
**Status**: Production Ready
