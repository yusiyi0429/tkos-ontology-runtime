# Remote production cutover

This directory deploys the authoritative Runtime as an isolated Compose project
on `127.0.0.1:8030`. PostgreSQL and MinIO use dedicated named volumes. The old
Memory (`8010`), AW (`8020`), Clark container, Nginx files, and source databases
remain available as rollback inputs.

The offline bundle contains linux/amd64 images, source archives, checksums, and
these deployment tools. It contains no environment file, bearer token, provider
key, database dump, or business row. The final merged database dump is generated
under the write fence and transferred separately with mode `0400`.

`ClarkOverlay.Dockerfile` supports an offline Clark build: the reviewed source is
first built to a temporary build image, then its standalone output is copied onto
the previously accepted linux/amd64 Clark runner base that already contains Node
and ffmpeg. The final manifest records the resulting image ID and both source refs.

Cutover order:

1. Extract the checksummed bundle into a new release directory and `docker load`
   the image archive.
2. Run `configure.py` as root. It copies the existing Memory scope and embedding
   and Clark-compatible read configuration inside the target, then generates
   fresh Runtime credentials. The provider key remains in the root-only target
   environment because the legacy adapter reads that existing variable directly.
3. Install the separately checksummed `merged.dump` into `private/`.
4. Run `scripts/start-candidate.sh`, then `scripts/verify.py --restart`.
5. Run `scripts/switch-memory-api.py` and verify the public endpoint.
6. Run `scripts/update-clark-env.py`, followed by
   `scripts/deploy-clark-image.sh RELEASE IMAGE`.
7. Verify the public Memory and Clark paths. Keep the old services stopped and
   intact. Disable `aw-memory-api.service` after the final checks so a host reboot
   cannot revive the retired writer. `scripts/rollback.sh RELEASE` restores
   routing, re-enables AW, and starts the prior Clark container without deleting
   the candidate.

The generated service principal has the `AGENT` role with only `read` and
`read_legacy_context` policy actions. It does not create a human identity or any
business object. DRI delivery, Outcome assessment, and MF closure remain separate
authorized Runtime actions for a later real identity and business-data rollout.

## Operations added alongside the cutover

**Backups.** `docker compose --profile ops run --rm backup` writes a custom-format
dump, its sha256 and a live row-count sidecar into `backups/` with mode 0400;
`backup-objects` mirrors both MinIO buckets beside it. The dump runs as the
admin identity on purpose: forty-nine governance tables carry
`FORCE ROW LEVEL SECURITY`, which applies to the table owner too, so the dump
needs an identity that bypasses RLS. A dump attempted as the migration owner
is not silently wrong by itself: pg_dump sets `row_security off`, the server
rejects the query and pg_dump exits 1. The hazard is the obvious fix for that
error — adding `--enable-row-security` makes the same command exit 0 and write
a dump containing zero governance rows. Both behaviours were reproduced
against this schema. The guard states the identity requirement up front so the
loud failure is never converted into a silent one, and the service refuses to
start unless its role is `rolsuper` or `rolbypassrls`.
Drive both from host cron, and copy the results off this host — neither MinIO
versioning nor Object Lock survives loss of the machine. Restore is only proven
when a drill has restored into an empty database and `db_admin.py fingerprint`
matches; the existing `restore` profile does the pg_restore half. That drill
has been run against this schema: 78 tables restored into an empty database
from a dump of a seeded scope, identical 77-table fingerprints, and row-level
security intact on both sides (49 tables ENABLE and FORCE, 53 policies).

**Statement counters.** The postgres service preloads `pg_stat_statements` and
`db_admin.py prepare` creates the extension. `db_admin.py statements-reset`
then `db_admin.py statements` report `calls` per statement for the application
role — the true number of statements executed, which is what a per-request SQL
budget is measured against. Table scan counters in `pg_stat_user_tables` are
not: one statement can scan many tables, and a nested loop scans its inner
relation once per outer row.

**Connection pool.** The runtime keeps one bounded pool per process
(`GOVERNED_POOL_MAX_SIZE`, default 10) instead of connecting per request. Keep
the sum across API and Worker below the server's `max_connections`. An
exhausted pool waits `GOVERNED_POOL_TIMEOUT_SECONDS` and then returns 503
rather than an unhandled 500. Setting `GOVERNED_POOL_MAX_SIZE=0` restores one
connection per request and builds no pool, which is the rollback switch.
Pooled connections are reused, so the pool clears the governance session GUCs
on return; `tests/test_governed_pool.py` asserts that a connection carrying
session-level identity hands the next caller a clean one.

**Credentials.** The application DSN and the embedding provider key are mounted
as docker secrets (`private/database-url`, `private/embedding-api-key`) and
referenced through `DATABASE_URL_FILE` / `MEMORY_EMBEDDING_API_KEY_FILE`, so
neither appears in the container environment where `docker inspect` prints it.
`configure.py` generates both. The ops profiles (`migrate`, `db-admin`,
`restore`, `backup`) still take their DSNs from the environment; they are
one-shot containers, not long-running services, and moving them is a separate
change.
