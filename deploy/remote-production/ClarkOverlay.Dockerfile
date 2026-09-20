# Build the reviewed Clark source separately as CLARK_BUILD_IMAGE, then place its
# standalone output on the already accepted linux/amd64 Clark runtime base. This
# avoids network package installation while preserving the base's Node and ffmpeg.
ARG CLARK_BUILD_IMAGE=clark-build:d5bddaa-amd64
ARG CLARK_RUNTIME_BASE=tkos/clark-web:stg-20260904-0e53c331-7ae20943-amd64
FROM ${CLARK_BUILD_IMAGE} AS build
FROM ${CLARK_RUNTIME_BASE}

USER root
COPY --from=build /app/public /app/public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone /app
COPY --from=build --chown=nextjs:nodejs /app/.next/static /app/.next/static
RUN chown -R nextjs:nodejs /app/public /app/.next /app/server.js
USER nextjs
