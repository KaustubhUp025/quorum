FROM python:3.12-slim AS base

WORKDIR /app

# Install the official GitLab CLI (`glab`) + git so the container uses the SAME
# MCP backend as the local CLI (glab mcp serve, semantic search). Without glab,
# make_client() auto-selects the REST lexical backend, which produces different
# results from the CLI — breaking webhook/CLI parity. `git` is required by
# GitLabGlabMCPClient._make_git_context (git init + remote add).
# Node.js 20 LTS + @zereight/mcp-gitlab remain as the zereight fallback tier.
ARG GLAB_VERSION=1.100.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
    && curl -fsSL "https://gitlab.com/gitlab-org/cli/-/releases/v${GLAB_VERSION}/downloads/glab_${GLAB_VERSION}_linux_amd64.tar.gz" \
       -o /tmp/glab.tar.gz \
    && tar -xzf /tmp/glab.tar.gz -C /tmp \
    && install -m 0755 /tmp/bin/glab /usr/local/bin/glab \
    && rm -rf /tmp/glab.tar.gz /tmp/bin \
    && glab --version \
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
