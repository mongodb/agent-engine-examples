FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir uv && \
    uv venv && \
    uv pip install .

FROM python:3.11-slim AS runner

# Create a non-root user and group with fixed IDs for consistency
RUN groupadd --gid 1001 appuser && useradd --uid 1001 --gid 1001 --create-home appuser

WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/src src
ENV PATH="/app/.venv/bin:$PATH"

# Give the non-root user ownership of /app so the app can access necessary files
RUN chown -R appuser:appuser /app

# Run the application as the non-root user
USER appuser

CMD ["python", "-m", "simple_agent.main"]

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir uv && \
    uv venv && \
    uv pip install .

FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir uv && \
    uv venv && \
    uv pip install .

FROM python:3.11-slim AS runner

# Add a non-root user and group
RUN groupadd --gid 1001 appuser && \
    useradd --uid 1001 --gid 1001 --create-home appuser

WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/src src

# Make sure /app is owned by the new user so writing is permitted
RUN chown -R appuser:appuser /app

ENV PATH="/app/.venv/bin:$PATH"

# Switch to the non-root user
USER appuser

CMD ["python", "-m", "simple_agent.main"]

WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/src src
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "simple_agent.main"]
