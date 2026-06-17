SOURCE_DIRS := src tests
RUN := uv run
PYRUN := $(RUN) python
RUFF_CMD := $(RUN) ruff
PYRIGHT_CMD := $(RUN) pyright
RUN_COV := $(RUN) pytest --cov=simple_agent --cov-report term-missing --cov-report html tests/

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
	$(PYRUN) -m simple_agent.main

docker-build:
	docker build -t simple-agent .

docker-run:
	docker run -p 8000:8000 --env-file .env simple-agent

clean:
	rm -rf .pytest_cache/ htmlcov/ __pycache__/
	find . -name "*.pyc" -delete
	find . -name "*.pyo" -delete

help:
	@echo "Available targets:"
	@echo "  install      - Install dependencies using uv"
	@echo "  test         - Run tests"
	@echo "  test-verbose - Run tests with verbose output"
	@echo "  cov          - Run tests with coverage"
	@echo "  lint         - Run ruff and pyright"
	@echo "  format       - Auto-format code"
	@echo "  ci-tests     - Run full CI pipeline (install, lint, cov)"
	@echo "  serve        - Run the agent locally"
	@echo "  docker-build - Build the Docker image"
	@echo "  docker-run   - Run the Docker container"
	@echo "  clean        - Clean up cache and temporary files"
	@echo "  help         - Show this help message"
