FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS builder

WORKDIR /build

# Cross-architecture wheels are prepared from this hashed lock on the build host.
# The named BuildKit context makes the container build network-independent.
COPY requirements.lock ./
COPY --from=wheelhouse / /wheels
RUN python -m pip install --dry-run --require-hashes --no-index \
    --find-links=/wheels -r requirements.lock

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS runtime-base

ARG VERSION=0.2.1
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="TKOS Ontology Runtime" \
    org.opencontainers.image.source="https://github.com/yusiyi0429/tkos-ontology-runtime" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.created="${BUILD_DATE}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_INDEX=1

RUN useradd --create-home --uid 10001 memory-service
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    python -m pip install --no-cache-dir --no-index --find-links=/wheels "tkos-memory-service[s3]==$VERSION"

# Installation is explicit; startup never seeds identities or runs migrations.
COPY docs/contracts/method-profile.json docs/contracts/tkos-method-0.1.md /opt/tkos/docs/contracts/
COPY docs/runtime-a2-registry.json docs/runtime-a3-registry.json docs/runtime-method-registry.json /opt/tkos/docs/

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
