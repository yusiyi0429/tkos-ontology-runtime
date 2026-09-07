FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
WORKDIR /build

# This layer changes only when the locked dependency graph changes.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --extra s3 --no-dev --no-emit-project --output-file requirements.txt \
    && python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements.txt

# Application changes rebuild only the small project wheel.
COPY README.md ./
COPY src ./src
RUN python -m pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.12-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 memory-service
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels

USER memory-service
WORKDIR /home/memory-service
STOPSIGNAL SIGTERM

FROM runtime-base AS worker
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=4 \
  CMD ["tkos-memory-worker-health"]
CMD ["tkos-memory-worker"]

FROM runtime-base AS runtime
EXPOSE 8010
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=4 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/v1/health', timeout=4)"]

CMD ["uvicorn", "memory_service_app.main:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1"]
