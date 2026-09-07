#!/usr/bin/env node
/* Actual Clark NarrativeClient, private queries over stdin, sanitized output only. */
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const Module = require("node:module");
const assert = require("node:assert/strict");
const hash = (value) => crypto.createHash("sha256").update(value).digest("hex");

async function main() {
  const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const state = JSON.parse(fs.readFileSync(config.state_file, "utf8"));
  assert.equal(state.local_only, true);
  const endpoint = new URL(config.api_url);
  assert.equal(endpoint.protocol, "http:");
  assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(endpoint.hostname));
  const cases = JSON.parse(fs.readFileSync(0, "utf8"));
  const root = fs.realpathSync(config.clark_root);
  const localRequire = Module.createRequire(path.join(root, "package.json"));
  const ts = localRequire("typescript");
  const originalResolve = Module._resolveFilename;
  const originalTs = Module._extensions[".ts"];
  const loaded = new Set();
  Module._resolveFilename = function(specifier, parent, ...rest) {
    if (specifier === "@/lib/graphknowledge" && parent?.filename === path.join(root, "src/lib/ontology/errors.ts")) {
      specifier = path.join(root, "src/lib/graphknowledge/errors.ts");
    } else if (specifier.startsWith("@/")) specifier = path.join(root, "src", specifier.slice(2));
    return originalResolve.call(this, specifier, parent, ...rest);
  };
  Module._extensions[".ts"] = function(mod, filename) {
    assert.ok(filename.startsWith(path.join(root, "src") + path.sep));
    loaded.add(filename);
    mod._compile(ts.transpileModule(fs.readFileSync(filename, "utf8"), { fileName: filename,
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
    }).outputText, filename);
  };
  try {
    const { NarrativeClient } = require(path.join(root, "src/lib/ontology/narrative.ts"));
    const client = new NarrativeClient({ baseUrl: config.api_url, apiKey: state.reader_token,
      tenant: state.tenant_id, org: state.company_id, timeoutMs: 30_000, warnings: [] });
    const rows = [];
    for (const [index, item] of cases.entries()) {
      for (const includeRaw of [false, true]) {
        const result = await client.narrative(item.query, { includeRaw });
        assert.equal(result.model, "deterministic-v1");
        assert.equal(result.tenant, state.tenant_id);
        assert.equal(result.org, state.company_id);
        assert.equal(result.hitPaths, item.paths);
        assert.equal(hash(result.rootStatement), item.root_sha256);
        assert.equal(hash(Buffer.from(result.text).subarray(0, item.background_utf8_bytes)), item.background_sha256);
        assert.equal(result.attempts, 1);
        if (includeRaw) assert.equal(result.text, result.raw);
        else { assert.equal(result.raw, ""); assert.equal(result.text, result.compressed); }
      }
      rows.push({ ordinal: index + 1, parsed: true, raw_and_normal_reads: 2, root_matched: true, background_matched: true });
    }
    const report = { status: "passed", actual_client: "src/lib/ontology/narrative.ts", actual_transport: "undici HTTP",
      fixed_vector_replay: true, real_embedding_model_accepted: false, cases: rows,
      loader: "Existing TypeScript compiler; real error class resolved to its definition without demo-store initialization",
      source_sha256: Object.fromEntries([...loaded].sort().map((filename) => [path.relative(root, filename), hash(fs.readFileSync(filename))])) };
    fs.writeFileSync(config.output_file, JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
    console.log(JSON.stringify({ status: "passed", checked_queries: rows.length }));
  } finally {
    Module._resolveFilename = originalResolve;
    if (originalTs) Module._extensions[".ts"] = originalTs;
    else delete Module._extensions[".ts"];
  }
}
main().then(() => process.exit(0)).catch((error) => {
  console.error(JSON.stringify({ status: "failed", error_type: error?.constructor?.name ?? "Error" }));
  process.exit(1);
});
