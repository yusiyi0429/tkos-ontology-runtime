# Source provenance

This repository starts from an independent source snapshot prepared on 2026-09-07. It does not include the source repositories' Git history.

- Base repository: [Erebus1102/TKOS_Memory_Service](https://github.com/Erebus1102/TKOS_Memory_Service), commit `78135ac8f399b4f6a66251bb5101aabb3cc23743`.
- The snapshot includes the subsequent, previously uncommitted governed Runtime implementation and independent acceptance harness. The source working tree's accepted run is `runtime-2e941f404803`; this base commit alone does not contain the full delivered implementation.
- The base repository records the original Memory core/migrations as extracted from [Erebus1102/TKOS_Agent_WorkSpace](https://github.com/Erebus1102/TKOS_Agent_WorkSpace), upstream commit `eed1513`.
- The base repository records its Clark compatibility adapter as originating from [VanillaCoca/clark](https://github.com/VanillaCoca/clark), reviewed commit `a32874505e2128bb9e733a2c707a9c684eff54ea`.

At the initial v0.1 export, all 87 production files under `src/` matched the accepted source snapshot. Their historical hashes remain in `docs/acceptance/production-source-sha256.json`.

Export-only changes make the acceptance interpreter selection and Docker project namespace independent, handle acceptance before an initial Git commit, strengthen ignored private-file patterns, and replace stale documentation with portable instructions. The historical Compose template is preserved under `deploy/legacy-memory/` rather than used as the current default deployment.

This export excludes private environment files, credentials, runtime databases, raw acceptance artifacts, local build caches, and deployment-specific operational records. Source attribution does not introduce or change a software license.

Runtime v0.2 extends the initial snapshot on `codex/runtime-v0.2-dri-delivery`, based on repository commit `a02d8b297394be7f86853ffad22406d2ff0a95a4`. The independently accepted working tree contains uncommitted changes; it no longer matches every v0.1 hash. See `docs/acceptance/runtime-v0.2-source-sha256.json` for the 135 files recorded in accepted run `runtime-a15a50851637`, and `docs/runtime-v0.2-acceptance-report.md` for its local acceptance boundary. No source history was rewritten.
