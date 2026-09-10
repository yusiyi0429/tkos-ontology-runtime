# Clark / Runtime v0.2 local joint acceptance

This harness creates an isolated `runtime-acceptance-clark-*` scope using the existing private PostgreSQL/MinIO acceptance environment. It does not alter the original Runtime acceptance runner or production modules.

```sh
.venv/bin/python acceptance/clark_v02/start.py --keep
```

If another validation server already uses `.next-clark-v02`, select a separate build directory with `--next-dist-dir .next-clark-main-check`. The directory must be a local `.next-*` name. A resumed run reuses the directory saved in its state unless explicitly overridden; the existing server is not stopped.

The command owns one API, receiver, Worker and loopback-only Next dev server. It prints only public URLs and a private `state.json` path. Keep the command running while browser and HTTP acceptance use these services. `state.json` points to per-identity login codes and the server-side identity mapping; those files have mode 0600 and must never be printed, committed or placed in browser bundles.

Next uses `.next-clark-v02` as its separate build directory and sets `TKOS_RUNTIME_CLARK_ORIGIN` to the actual loopback URL. This keeps strict Origin/Host checks independent of Next's internal request URL normalization.

To stop exactly this run:

```sh
.venv/bin/python acceptance/clark_v02/start.py --stop /absolute/path/to/state.json
```

Once that owner has exited, resume the same persisted business objects and identity codes without reseeding:

```sh
.venv/bin/python acceptance/clark_v02/start.py --resume-state /absolute/path/to/state.json --keep
```

Resume retains the Clark port, starts a fresh owned API/receiver/Worker, and updates the private state with their URLs and PIDs. It refuses to run while the previous owner still exists. Resume does not reset a completed delivery chain; use a new run for another full mutation suite.

For a read-only persistence checkpoint, call `check.py STATE --checkpoint-browser capture` before stopping, then `check.py STATE --checkpoint-browser verify` after resuming and before another browser mutation. It compares canonical SHA-256 hashes of the WorkItem, Outcome and Feedback object responses and refuses to overwrite an existing baseline.

The verifier explicitly distinguishes raw equality from the one known additive `feedback.resolution_decision_revision: null` projection. If that field was absent before restart, the report names the schema addition and requires every pre-existing field, version and referenced receipt to remain identical. Any other difference fails verification.

Provisioning seeds only identities, authority and the bootstrap helper's unused starting fact. Each of the `browser` and `http` WorkItems has its own CompanyOutcome, commitments, signatures, activation effects and investigating FeedbackThread, created through real Runtime HTTP. No Deliverable, acceptance or Outcome assessment is seeded. The `browser` chain belongs to browser validation; the independent `http` chain belongs to protocol/negative validation.

After the first browser readiness check, run the protocol suite once against the unused `http` chain:

```sh
.venv/bin/python acceptance/clark_v02/check.py /absolute/path/to/state.json
```

It checks independent cookies, shared-password separation, Origin and role spoofing, cross-domain and wrong-person rejection, real evidence upload/download, DRI v1/return/v2 acceptance, original-receipt replay, stale commands, independent Outcome assessment and MF closure. Database inspection uses read-only transactions under the application role. It does not mutate the browser chain.

`browser.cjs` drives the real `/delivery` UI through an existing Chromium CDP connection, using an already-installed Playwright module supplied via `TKOS_PLAYWRIGHT_MODULE`. It consumes private login codes inside the process without printing them. Run the `delivery`, `outcome`, and `mf` phases in that order against a new browser chain. It performs no business writes through direct HTTP: the `delivery` phase forwards a real v2 submission and deliberately aborts only the response, then reloads the page and verifies that manual retry returns the same Receipt. Screenshots and the browser report are stored in the ignored per-run artifact directory.

```sh
TKOS_PLAYWRIGHT_MODULE=/path/to/existing/playwright node acceptance/clark_v02/browser.cjs /absolute/path/to/state.json ws://127.0.0.1:PORT/devtools/browser/ID delivery
# Repeat with outcome, then mf, keeping the same browser and state.
```

The optional `outcome_resume` phase only resumes an already-recorded 72-value observation still present in the open browser form; it is for diagnosing interrupted browser checks, not a substitute for a fresh full run.

After browser operations have finished, verify failure handling explicitly:

```sh
.venv/bin/python acceptance/clark_v02/check.py /absolute/path/to/state.json --offline-only
```

This temporarily pauses only the recorded API process for that run, verifies Clark returns `503 RUNTIME_UNAVAILABLE` without a mock result, then resumes the API in `finally` and verifies reads recover. Coordinate this check because all identities in the same run share that API. Reports and sanitized HTTP transcripts remain in the ignored per-run artifacts directory.

After the browser completes delivery, two independent Outcome assessments and MF closure, run the final physical oracle:

```sh
.venv/bin/python acceptance/clark_v02/browser_oracle.py /absolute/path/to/state.json
```

It uses the application database role in a `REPEATABLE READ READ ONLY` transaction, then reads the exact MF Decision projection through the BFF. The expected browser evidence is two Deliverable revisions, two delivery reviews, `not_achieved` then `achieved` assessments, one independent MF acceptance and one closure. Every key action receipt is checked against the named human principal. The oracle performs no business mutation and does not mark a release or production deployment accepted.

Next uses a private data directory, demo mode, model/search mocks and explicit empty API credentials. Runtime integration is real HTTP; demo model outputs are never used as acceptance evidence. The independent Runtime credentials are available only to the Clark BFF, and a separate random shared Clark password cannot confer Runtime authority.

This is local joint acceptance with synthetic named identities. It is not SSO provisioning, a remote deployment or production acceptance.

The authority group also checks the Clark v0.142.0 command-brief boundary: a shared Clark CEO cookie cannot dispatch a Runtime WorkItem, a Runtime personal cookie cannot invoke Clark's CEO-only command RPCs, and raw Clark issued-order fields cannot bypass the BFF action allowlist. See [the command-brief integration review](../../docs/clark-command-brief-runtime-review.md) for the source/identity/version mapping still required before automatic handoff.

The 2026-09-10 A1 recheck passed all four HTTP groups against a dedicated existing
`tkos_a1_*` database with a fresh synthetic scope. Codex selected that environment
through a local QA adapter; the application, startup, BFF and assertions were
unchanged. The existing 0017 demo database was not upgraded. The default launcher
still consumes `.runtime-acceptance/env.json`; do not mistake its current schema
for proof of A1 readiness. See [the A1 result](../../docs/acceptance/clark-runtime-a1-20260910.json)
for exact source, browser-smoke, recovery and deployment boundaries.
