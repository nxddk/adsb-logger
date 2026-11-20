FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOG_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/adsb-logger-entrypoint

ENTRYPOINT ["adsb-logger-entrypoint"]
CMD ["adsb-logger"]

FROM base AS test
ENV PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache" \
    RUFF_CACHE_DIR=/tmp/ruff-cache
RUN pip install --no-cache-dir '.[dev]'
COPY tests/ ./tests/
CMD ["pytest", "-q"]

FROM base AS runtime
