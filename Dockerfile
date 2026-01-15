# Use Python 3.12 slim image for smaller size
FROM python:3.12-slim

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

# Copy dependency files first for better layer caching
COPY pyproject.toml ./

# Install dependencies using uv
RUN uv pip install -e .

# Copy the application source code
COPY src/ ./src/
COPY tests/ ./tests/
COPY pytest.ini ./

# Create directory for Garmin tokens
RUN mkdir -p /root/.garminconnect && \
    chmod 700 /root/.garminconnect

# Expose the application (if needed for network communication)
# Note: MCP servers typically communicate via stdio, so no port exposure is usually needed
# EXPOSE 8000

# Set the entrypoint to run the MCP server
ENTRYPOINT ["python", "-m", "garmin_mcp"]

# Health check (optional - adjust based on your needs)
# HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
#   CMD python -c "import sys; sys.exit(0)"
