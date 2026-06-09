FROM python:3.12-slim AS base

WORKDIR /app

# Install Node.js 20 LTS (pinned via NodeSource) for the @zereight/mcp-gitlab MCP server.
# Debian Bookworm ships EOL Node 18 from APT; NodeSource gives us a supported LTS.
# @zereight/mcp-gitlab is pinned to avoid silent supply-chain updates.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @zereight/mcp-gitlab@2.1.18 \
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
