import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {
  PINNED_CLI_VERSION, PINNED_CLI_SHA256, POLICY_MODEL, EXTRA_CONFIG_OVERRIDES,
  EXTRA_DISABLED_FEATURES, REQUIRED_TOOL_DISABLED_FEATURES, EXTRA_TOOL_POLICY_ARGUMENTS,
  TOOL_POLICY_SOURCE, CodexToolPolicyError, buildOperatorCatalog, verifyOperatorReadback,
  verifyBinaryAndCatalog, verifyCatalogDigest, prepareOperatorCatalog,
  checkedMetadataResult,
} from '../codex-tool-policy.mjs';

// A model metadata contract fixture, not a runtime tool-registry observation.
const bundled = () => ({ models: [{ slug: 'other-model' }, {
  slug: POLICY_MODEL, display_name: 'GPT-6-Luna', shell_type: 'unified_exec',
  tool_mode: 'code_mode_only', apply_patch_tool_type: 'freeform',
  experimental_supported_tools: ['clock', 'send_message_to_user_async'],
  use_responses_lite: true, multi_agent_version: 'v2', supports_search_tool: true,
  model_messages: { opaque: ['unchanged model metadata'] },
}] });
const sha = contents => createHash('sha256').update(contents).digest('hex');
const codeIs = code => error => error instanceof CodexToolPolicyError && error.code === code;

test('operator catalog changes exactly three Luna fields and preserves opaque metadata', () => {
  const original = bundled();
  const snapshot = structuredClone(original);
  const catalog = buildOperatorCatalog(original);
  assert.deepEqual(original, snapshot);
  assert.equal(catalog.models.length, 1);
  assert.equal(catalog.models[0].slug, POLICY_MODEL);
  const expected = structuredClone(original.models[1]);
  Object.assign(expected, { apply_patch_tool_type: null, experimental_supported_tools: [], tool_mode: 'direct' });
  assert.deepEqual(catalog.models[0], expected);
  assert.equal(verifyOperatorReadback(original, catalog), true);
});

test('missing/duplicate identities and unexpected bundled policy drift fail closed', () => {
  const empty = { models: [] };
  assert.throws(() => buildOperatorCatalog(empty), codeIs('bundled_luna_missing_or_duplicated'));
  const duplicate = bundled(); duplicate.models.push(structuredClone(duplicate.models[1]));
  assert.throws(() => buildOperatorCatalog(duplicate), codeIs('bundled_luna_missing_or_duplicated'));
  for (const mutation of [m => { delete m.tool_mode; }, m => { m.tool_mode = 'direct'; },
    m => { m.shell_type = 'disabled'; }, m => { m.use_responses_lite = false; },
    m => { m.experimental_supported_tools = []; }, m => { m.apply_patch_tool_type = null; }]) {
    const drift = bundled(); mutation(drift.models[1]);
    assert.throws(() => buildOperatorCatalog(drift), codeIs('bundled_luna_metadata_drift'));
  }
});

test('startup readback rejects extra models, tools, model fallback, and altered non-policy fields', () => {
  for (const mutate of [c => c.models.push({ slug: 'fallback' }),
    c => { c.models[0].slug = 'fallback'; }, c => { c.models[0].tool_mode = 'code_mode_only'; },
    c => { c.models[0].apply_patch_tool_type = 'freeform'; },
    c => c.models[0].experimental_supported_tools.push('clock'),
    c => { c.models[0].model_messages.opaque = ['changed']; },
    c => { c.models[0].unexpected_field = true; }]) {
    const original = bundled(), readback = buildOperatorCatalog(original); mutate(readback);
    assert.throws(() => verifyOperatorReadback(original, readback), codeIs('operator_catalog_readback_mismatch'));
  }
});

test('catalog digest detects mutation and missing proof independently of model JSON validity', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'codex-tool-policy-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const catalogPath = path.join(dir, 'catalog.json');
  const contents = JSON.stringify(buildOperatorCatalog(bundled()));
  await writeFile(catalogPath, contents);
  const expectedCatalogSha256 = sha(contents);
  assert.equal(await verifyCatalogDigest({ catalogPath, expectedCatalogSha256 }), true);
  await assert.rejects(verifyCatalogDigest({ catalogPath }), codeIs('catalog_digest_evidence_missing'));
  await writeFile(catalogPath, contents + ' ');
  await assert.rejects(verifyCatalogDigest({ catalogPath, expectedCatalogSha256 }), codeIs('operator_catalog_digest_mismatch'));
  await assert.rejects(verifyCatalogDigest({ catalogPath: dir, expectedCatalogSha256 }), codeIs('operator_catalog_not_regular_file'));
  await assert.rejects(verifyCatalogDigest({ catalogPath: 'relative.json', expectedCatalogSha256 }), codeIs('operator_catalog_path_not_absolute'));
});

test('a newly digested catalog still rejects a native-tool policy violation', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'codex-tool-policy-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const catalogPath = path.join(dir, 'catalog.json');
  const catalog = buildOperatorCatalog(bundled()); catalog.models[0].tool_mode = 'code_mode_only';
  const contents = JSON.stringify(catalog); await writeFile(catalogPath, contents);
  await assert.rejects(verifyCatalogDigest({ catalogPath, expectedCatalogSha256: sha(contents) }),
    codeIs('operator_catalog_policy_mismatch'));
});

test('unpinned executable is rejected before any child command or inference can start', async t => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'codex-tool-policy-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const binaryPath = path.join(dir, 'codex.exe'); await writeFile(binaryPath, 'not the pinned executable');
  let calls = 0;
  await assert.rejects(prepareOperatorCatalog({ binaryPath, directory: dir,
    runMetadata: async () => { calls++; throw new Error('must not execute'); } }), codeIs('cli_binary_digest_mismatch'));
  assert.equal(calls, 0);
  await assert.rejects(verifyBinaryAndCatalog({ binaryPath, catalogPath: binaryPath, expectedCatalogSha256: sha('x') }),
    codeIs('cli_binary_digest_mismatch'));
  await assert.rejects(verifyBinaryAndCatalog({ binaryPath }), codeIs('catalog_digest_evidence_missing'));
});

test('required source/config closure stays unavailable while effective exec MCP configuration is unproved', () => {
  assert.equal(PINNED_CLI_VERSION, 'codex-cli 0.167.0-alpha.4.1');
  assert.match(PINNED_CLI_SHA256, /^[0-9a-f]{64}$/);
  assert.equal(TOOL_POLICY_SOURCE.sourceClosureVerified, false);
  assert.equal(TOOL_POLICY_SOURCE.remainingBlocker, 'effective_exec_mcp_configuration_not_verified');
  assert.equal(TOOL_POLICY_SOURCE.unifiedExecIsBackendOnly, true);
  assert.ok(TOOL_POLICY_SOURCE.emptyTableDoesNotClear.includes('/config/src/merge.rs#'));
  assert.ok(REQUIRED_TOOL_DISABLED_FEATURES.includes('shell_tool'));
  assert.ok(!REQUIRED_TOOL_DISABLED_FEATURES.includes('unified_exec'));
  assert.ok(EXTRA_CONFIG_OVERRIDES.includes('tools.experimental_request_user_input.enabled=false'));
  assert.ok(EXTRA_CONFIG_OVERRIDES.includes('agents.enabled=false'));
  assert.ok(EXTRA_CONFIG_OVERRIDES.includes('cloud.skills.enabled=false'));
  for (const feature of EXTRA_DISABLED_FEATURES) {
    assert.ok(REQUIRED_TOOL_DISABLED_FEATURES.includes(feature));
    assert.ok(EXTRA_TOOL_POLICY_ARGUMENTS.includes(feature));
  }
});

test('metadata process evidence rejects undrained, failed, noisy, or oversized commands', () => {
  const valid = { code: 0, drained: true, stdout: '{}', stderr: '' };
  assert.equal(checkedMetadataResult(valid), '{}');
  for (const mutation of [r => { r.code = 1; }, r => { r.code = null; },
    r => { r.drained = false; }, r => { delete r.drained; }, r => { r.stderr = 'config warning'; },
    r => { delete r.stdout; }, r => { r.stdout = 'x'.repeat(8 * 1024 * 1024 + 1); }]) {
    const result = { ...valid }; mutation(result);
    assert.throws(() => checkedMetadataResult(result), codeIs('cli_metadata_not_verified'));
  }
});
