# Stage 1: Build virtual environment
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repository root (see docker-compose.yml). Preserve the
# repo layout so the `../shared` path dependency is still resolvable.
COPY shared /app/shared
COPY auth0_api/pyproject.toml auth0_api/uv.lock /app/auth0_api/

RUN pip install --no-cache-dir "uv==0.12.22" && \
    uv venv /opt/venv && \
    cd /app/auth0_api && \
    uv export --locked --no-dev --no-emit-project \
        --no-emit-package shared-secrets \
        -o /tmp/requirements.txt && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r /tmp/requirements.txt && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache --no-deps /app/shared

# Stage 2: Minimal runtime
FROM python:3.12-slim AS runner

WORKDIR /app/auth0_api

# Create non-root user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /sbin/nologin -d /app/auth0_api appuser

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code with non-root ownership
COPY --chown=appuser:appgroup auth0_api/ .

USER appuser

EXPOSE 8001

CMD ["python", "main.py"]
