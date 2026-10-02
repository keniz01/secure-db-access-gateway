# Stage 1: Build virtual environment
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repository root (see docker-compose.yml). Preserve the
# repo layout so the `../shared` path dependency is still resolvable.
COPY shared /app/shared
COPY sql_query_api/pyproject.toml sql_query_api/uv.lock /app/sql_query_api/

# Resolve from uv.lock, NOT from pyproject.toml. `uv pip compile pyproject.toml`
# re-resolves on every build, so the copied lock was decorative: builds were
# unreproducible, the version nobody audited is not the version that ships, and
# a lock bump could never be proven to change what is deployed.
# --locked (not --frozen) makes the lock authoritative AND fails the build when
# it is stale: under --frozen, a dependency added to pyproject.toml but not
# relocked would be silently omitted from the export and then resolved unpinned
# by the editable install, reopening exactly this hole with no error.
RUN pip install --no-cache-dir "uv==0.12.22" && \
    uv venv /opt/venv && \
    cd /app/sql_query_api && \
    uv export --locked --no-dev --no-emit-project \
        --no-emit-package shared-secrets \
        -o /tmp/requirements.txt && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r /tmp/requirements.txt && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache --no-deps /app/shared

# Stage 2: Minimal runtime
FROM python:3.12-slim AS runner

WORKDIR /app/sql_query_api

# Create non-root user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /sbin/nologin -d /app/sql_query_api appuser

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code with non-root ownership
COPY --chown=appuser:appgroup sql_query_api/ .

USER appuser

EXPOSE 8002

CMD ["python", "main.py"]
