# syntax=docker/dockerfile:1

# Both stages must use the same Python: the virtualenv copied into the runtime
# stage points at this interpreter.
ARG PYTHON_IMAGE=python:3.12-slim

# Pin uv so rebuilding the same commit installs the same way (Dependabot
# bumps this tag).
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install the locked runtime dependencies first, so this layer is reused until
# uv.lock changes. --locked fails the build if uv.lock is out of date instead of
# silently resolving newer versions.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then install the project itself (non-editable, so the runtime stage doesn't
# need the source tree).
COPY README.md LICENSE ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# Create directory for Garmin tokens
RUN mkdir -p /root/.garminconnect && \
    chmod 700 /root/.garminconnect

# The image defaults to stdio (Claude Desktop, Inspector). Set
# GARMIN_MCP_TRANSPORT=streamable-http and GARMIN_MCP_HOST=0.0.0.0 to serve
# over this port, behind an authenticating reverse proxy.
EXPOSE 8000

# Only the HTTP transports have something to probe; under stdio the container
# reports healthy as long as it runs.
COPY --chmod=755 <<'EOF' /usr/local/bin/garmin-mcp-healthcheck
#!/usr/bin/env python3
import os
import sys
import urllib.request

if os.environ.get("GARMIN_MCP_TRANSPORT", "stdio").strip().lower() == "stdio":
    sys.exit(0)
host = os.environ.get("GARMIN_MCP_HOST", "127.0.0.1")
host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
if ":" in host:
    host = f"[{host}]"
port = os.environ.get("GARMIN_MCP_PORT", "8000")
urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=4)
EOF
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["garmin-mcp-healthcheck"]

ENTRYPOINT ["garmin-mcp"]
