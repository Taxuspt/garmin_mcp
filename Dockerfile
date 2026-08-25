# Use Python 3.12 slim image for smaller size
FROM python:3.12-slim AS base

# Note: .dockerignore is symlinked to .gitignore for unified exclusion rules

# Set working directory
WORKDIR /app

# Install uv for faster dependency management
# https://github.com/astral-sh/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

# Copy dependency files and README first for better layer caching
COPY pyproject.toml README.md ./

# Copy the application source code (needed for editable install)
COPY src/ ./src/

# Install dependencies using uv
RUN uv pip install -e .

# Create directory for Garmin tokens
RUN mkdir -p /root/.garminconnect && \
    chmod 700 /root/.garminconnect

# ---------------------------------------------------------------------------
# test stage — adds pytest/ruff on top of base. Never shipped as the runtime
# image; only built via `docker compose run --rm test|lint` (target: test).
# See ai-docs/testing.md.
# ---------------------------------------------------------------------------
FROM base AS test

RUN uv pip install \
    pytest \
    pytest-asyncio \
    pytest-mock \
    pytest-timeout \
    pytest-xdist \
    ruff

COPY tests/ ./tests/
COPY pytest.ini ./
COPY dxt/ ./dxt/
COPY garmin-mcp.dxt ./

ENTRYPOINT ["pytest"]

# ---------------------------------------------------------------------------
# runtime stage (default build target — last stage in the file) — no test
# deps, no tests/ directory. This is what `docker compose up` / `build`
# without --target produces.
# ---------------------------------------------------------------------------
FROM base AS runtime

# Expose the HTTP port. The image defaults to stdio (Claude Desktop, Inspector);
# set GARMIN_MCP_TRANSPORT=streamable-http to serve over this port (e.g. in k8s).
# EXPOSE 8000

# Set the entrypoint to run the MCP server
ENTRYPOINT ["garmin-mcp"]

# Health check (optional - adjust based on your needs)
# HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
#   CMD python -c "import sys; sys.exit(0)"
