FROM python:3.12-slim AS base

WORKDIR /app

# Install Node.js (LTS) for the @zereight/mcp-gitlab MCP server subprocess.
# Pre-install the package globally so the first review has no download latency.
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        npm \
    && npm install -g @zereight/mcp-gitlab \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy the full package source before installing.
# hatchling (the build backend) needs src/quorum/ present to build a real wheel.
# pyproject.toml and README.md are also required by hatchling at build time.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN uv pip install --system --no-cache .

# Non-root user
RUN useradd --create-home --shell /bin/bash quorum
USER quorum

EXPOSE 8080

# Default to serving the webhook (Cloud Run mode)
CMD ["python", "-m", "quorum", "serve"]
