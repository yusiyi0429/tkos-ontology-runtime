#!/usr/bin/env node
/* Execute Clark's real NarrativeClient against a private acceptance Runtime.
 * Existing TypeScript is transpiled in memory; this installs no dependencies.
 * The error-only GraphKnowledge re-export resolves to the same source class,
 * avoiding unrelated demo store initialization while retaining real undici HTTP.
 */
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const Module = require("node:module");
const assert = require("node:assert/strict");

async function main() {
  const configPath = process.argv[2];
  if (!configPath) throw new Error("Usage: node clark_client.cjs PRIVATE_CONFIG.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const root = fs.realpathSync(config.clark_root);
  const source = path.join(root, "src/lib/ontology/narrative.ts");
  const fixture = JSON.parse(fs.readFileSync(config.fixture_file, "utf8"));
  const localRequire = Module.createRequire(path.join(root, "package.json"));
  const ts = localRequire("typescript");
  const originalResolve = Module._resolveFilename;
  const originalTs = Module._extensions[".ts"];
  const loaded = new Set();
  Module._resolveFilename = function(specifier, parent, ...rest) {
    if (specifier === "@/lib/graphknowledge" && parent?.filename === path.join(root, "src/lib/ontology/errors.ts")) {
      specifier = path.join(root, "src/lib/graphknowledge/errors.ts");
    } else if (specifier.startsWith("@/")) {
      specifier = path.join(root, "src", specifier.slice(2));
    }
    return originalResolve.call(this, specifier, parent, ...rest);
  };
  Module._extensions[".ts"] = function(mod, filename) {
    if (!filename.startsWith(path.join(root, "src") + path.sep)) throw new Error("Unexpected TypeScript source outside Clark src");
    const code = fs.readFileSync(filename, "utf8");
    loaded.add(filename);
    const compiled = ts.transpileModule(code, { fileName: filename, compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
    } });
    mod._compile(compiled.outputText, filename);
  };
  try {
    const { NarrativeClient } = require(source);
    const client = new NarrativeClient({
      baseUrl: config.api_url, apiKey: fixture.actors.ceo.token,
      tenant: fixture.tenant_id, org: fixture.company_id, timeoutMs: 10_000, warnings: [],
    });
    const results = [];
    for (const includeRaw of [false, true, true]) {
      const result = await client.narrative(config.query, { includeRaw });
      assert.equal(result.tenant, fixture.tenant_id);
      assert.equal(result.org, fixture.company_id);
      assert.equal(result.model, "deterministic-v1");
      assert.ok(result.text.trim());
      assert.equal(result.attempts, 1);
      assert.ok(result.compressedChars > 0);
      if (includeRaw) { assert.ok(result.raw.trim()); assert.equal(result.text, result.raw); }
      else { assert.equal(result.raw, ""); assert.equal(result.text, result.compressed); }
      for (const marker of config.required_markers ?? []) assert.ok(result.text.includes(marker), "Authoritative marker absent from real Clark client result");
      for (const marker of config.forbidden_markers ?? []) assert.ok(!result.text.includes(marker), "Draft or unauthorized marker leaked through Clark client");
      results.push({ include_raw: includeRaw, text_sha256: crypto.createHash("sha256").update(result.text).digest("hex"),
        text_chars: result.text.length, model: result.model, attempts: result.attempts, hit_paths: result.hitPaths,
        lateral_nodes: result.lateralNodes, raw_present: !!result.raw, source_runtime: true });
    }
    const report = { status: "passed", actual_client: "src/lib/ontology/narrative.ts", actual_transport: "undici HTTP",
      loader: "Existing Clark TypeScript compiler; real error class import narrowed to its defining source",
      model_acceptance: false, results, loaded_sources: [...loaded].sort().map((filename) => ({
        path: path.relative(root, filename), sha256: crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex"),
      })) };
    fs.writeFileSync(config.output_file, JSON.stringify(report, null, 2) + "\n", { mode: 0o600 });
    console.log(JSON.stringify({ status: "passed", real_clark_client_calls: results.length, model_acceptance: false }));
  } finally {
    Module._resolveFilename = originalResolve;
    if (originalTs) Module._extensions[".ts"] = originalTs;
    else delete Module._extensions[".ts"];
  }
}
main().catch((error) => {
  // Never print arbitrary upstream bodies, exception messages or configuration.
  console.error(JSON.stringify({ status: "failed", error_type: error?.constructor?.name ?? "Error" }));
  process.exitCode = 1;
});
