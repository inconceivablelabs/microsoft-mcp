FROM python:3.12-slim

RUN apt-get update && apt-get install -y dumb-init && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" mcpuser

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

COPY src/ src/
COPY authenticate.py ./
RUN uv sync --no-dev --frozen

RUN mkdir -p /data && chown -R mcpuser:mcpuser /app /data

ENV MICROSOFT_MCP_TOKEN_CACHE=/data/token_cache.json

USER mcpuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD pgrep -f "python.*microsoft_mcp" || exit 1

ENTRYPOINT ["dumb-init", "--"]
CMD [".venv/bin/python", "-m", "microsoft_mcp"]
