FROM python:3.11-slim AS builder

WORKDIR /app
ENV UV_EXCLUDE_NEWER="7 days"
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock uv.toml ./
COPY README.md ./
COPY src/ src/

RUN pip install --no-cache-dir "uv==0.11.8" && \
    uv sync --locked --no-dev --no-editable

FROM python:3.11-slim AS runner

RUN groupadd --gid 1001 appuser && useradd --uid 1001 --gid 1001 --create-home appuser

WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/src src
ENV PATH="/app/.venv/bin:$PATH"

RUN chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "simple_agent.main"]
