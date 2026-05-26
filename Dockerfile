FROM python:3.12-slim AS base

WORKDIR /app

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency metadata first for layer caching
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# Copy source
COPY src/ ./src/

# Non-root user
RUN useradd --create-home --shell /bin/bash quorum
USER quorum

EXPOSE 8080

# Default to serving the webhook (Cloud Run mode)
CMD ["python", "-m", "quorum", "serve"]
