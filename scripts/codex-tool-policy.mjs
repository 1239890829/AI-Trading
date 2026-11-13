/** Pinned, metadata-only admission for the local Codex text bridge. */
import { createHash } from 'node:crypto';
import { lstat, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { isDeepStrictEqual } from 'node:util';

export const PINNED_CLI_VERSION = 'codex-cli 0.167.0-alpha.4.1';
export const PINNED_CLI_SHA256 = '83f5c2dd2ac2902e0d96ba5d8ccdd27d513f57df4a9ab73e06a5a518028e9a2f';
export const POLICY_MODEL = 'gpt-6-luna';
const SOURCE_COMMIT = 'd2c035639366e0bf2c316aa2065c24aabc12bcdc';
const SOURCE_ROOT = `https://github.com/openai/codex/blob/${SOURCE_COMMIT}/codex-rs/`;
const MAX_CATALOG_BYTES = 8 * 1024 * 1024;
const OVERRIDDEN_MODEL_FIELDS = Object.freeze(['apply_patch_tool_type', 'experimental_supported_tools', 'tool_mode']);

export const EXTRA_CONFIG_OVERRIDES = Object.freeze([
  'tools.update_plan.enabled=false',
  'tools.experimental_request_user_input.enabled=false',
  'agents.enabled=false',
  'cloud.skills.enabled=false',
  'orchestrator.mcp.enabled=false',
]);
export const EXTRA_DISABLED_FEATURES = Object.freeze([
  'deferred_executor', 'current_time_reminder', 'token_budget', 'send_message_to_user_async',
  'multi_env_copy_file', 'executor_capability_discovery', 'executor_capability_discovery_v2',
]);
// Every native registry family in spec_plan.rs and the installed extension contributors.
// unified_exec is deliberately absent: shell_tool=false vetoes registration before
// unified_exec chooses a resumable or one-shot execution backend (L1244-1274).
export const REQUIRED_TOOL_DISABLED_FEATURES = Object.freeze([
  'shell_tool', 'code_mode', 'code_mode_host', 'code_mode_only',
  'multi_agent', 'multi_agent_v2', 'agent_message_board', 'agent_message_board_mcp',
  'apps', 'plugins', 'hooks', 'memories', 'image_generation', 'view_image',
  'sleep_tool', 'tool_suggest', 'request_permissions_tool', 'standalone_web_search',
  ...EXTRA_DISABLED_FEATURES,
]);
export const EXTRA_TOOL_POLICY_ARGUMENTS = Object.freeze([
  ...EXTRA_CONFIG_OVERRIDES.flatMap(value => ['-c', value]),
  ...EXTRA_DISABLED_FEATURES.flatMap(value => ['--disable', value]),
]);

// This is a source proof for one signed executable and fixed invocation shape,
// not a claim that debug models captures a live inference request's tools array.
export const TOOL_POLICY_SOURCE = Object.freeze({
  tag: 'rust-v0.167.0-alpha.4.1', commit: SOURCE_COMMIT,
  sourceClosure: 'pinned-fresh-local-exec-empty-registry-v1',
  sourceClosureVerified: false,
  remainingBlocker: 'effective_exec_mcp_configuration_not_verified',
  unifiedExecIsBackendOnly: true,
  directSerde: `${SOURCE_ROOT}protocol/src/openai_models.rs#L359-L365`,
  modelToolModePrecedence: `${SOURCE_ROOT}core/src/tools/mod.rs#L66-L88`,
  registrySources: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L122-L189`,
  shellRegistrationVeto: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L1244-L1274`,
  coreUtilityRegistration: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L1296-L1454`,
  noServerResourceTools: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L1278-L1293`,
  codeModeWrappers: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L1019-L1107`,
  extensionRegistration: `${SOURCE_ROOT}app-server/src/extensions.rs#L72-L142`,
  extensionFiltering: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L1655-L1683`,
  historyNotes: `${SOURCE_ROOT}ext/history-notes/src/extension.rs#L47-L68`,
  tokenBudgetDisabled: `${SOURCE_ROOT}core/src/config/mod.rs#L2966-L2972`,
  memoryTools: `${SOURCE_ROOT}ext/memories/src/extension.rs#L137-L161`,
  goalTools: `${SOURCE_ROOT}ext/goal/src/runtime.rs#L122-L132`,
  skillToolGate: `${SOURCE_ROOT}ext/skills/src/preparation.rs#L23-L62`,
  skillTools: `${SOURCE_ROOT}ext/skills/src/tools/mod.rs#L57-L95`,
  discoveryV2Gate: `${SOURCE_ROOT}core/src/session/mcp.rs#L543-L553`,
  multiAgentOverride: `${SOURCE_ROOT}core/src/config/mod.rs#L1687-L1713`,
  appsMcp: `${SOURCE_ROOT}ext/mcp/src/lib.rs#L37-L57`,
  pluginMcp: `${SOURCE_ROOT}ext/mcp/src/plugin.rs#L451-L471`,
  freshExecDefaults: `${SOURCE_ROOT}exec/src/lib.rs#L1462-L1488`,
  ignoreUserEnvironmentConfig: `${SOURCE_ROOT}exec/src/lib.rs#L723-L736`,
  localEmptyCapabilityRoots: `${SOURCE_ROOT}exec-server/src/environment.rs#L789-L800`,
  capabilityRootsReadback: `${SOURCE_ROOT}exec-server/src/environment.rs#L913-L921`,
  finalModelSpecs: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L630-L662`,
  metadataToolInfo: `${SOURCE_ROOT}core/src/tools/spec_plan.rs#L470-L476`,
  execWarningMapping: `${SOURCE_ROOT}exec/src/event_processor_with_jsonl_output.rs#L430-L509`,
  configLayerMerge: `${SOURCE_ROOT}config/src/state.rs#L637-L645`,
  emptyTableDoesNotClear: `${SOURCE_ROOT}config/src/merge.rs#L101-L153`,
  systemAndCloudConfigLayers: `${SOURCE_ROOT}config/src/loader/mod.rs#L275-L291`,
  userConfigOnlyIgnored: `${SOURCE_ROOT}config/src/loader/mod.rs#L518-L534`,
  managedConfigAfterSessionFlags: `${SOURCE_ROOT}config/src/loader/mod.rs#L430-L483`,
  mcpListMayUseHttp: `${SOURCE_ROOT}cli/src/mcp_cmd.rs#L690-L718`,
  windowsConfigNamespace: `${SOURCE_ROOT}config/src/loader/mod.rs#L808-L839`,
  windowsLegacyConfigIgnored: `${SOURCE_ROOT}config/src/loader/layer_io.rs#L65-L105`,
  embeddedPackageConfig: `${SOURCE_ROOT}config/src/loader/mod.rs#L187-L202`,
  execCloudConfigBootstrap: `${SOURCE_ROOT}exec/src/lib.rs#L501-L522`,
  cloudConfigCanRefresh: `${SOURCE_ROOT}cloud-config/src/bundle_loader.rs#L126-L155`,
});

export class CodexToolPolicyError extends Error {
  constructor(code) {
    super(code);
    this.name = 'CodexToolPolicyError';
    this.code = code;
  }
}
const reject = code => { throw new CodexToolPolicyError(code); };
const digest = contents => createHash('sha256').update(contents).digest('hex');
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function modelEntries(catalog) {
  if (!object(catalog) || !Array.isArray(catalog.models)
      || catalog.models.some(model => !object(model) || typeof model.slug !== 'string')) {
    reject('invalid_model_metadata');
  }
  return catalog.models;
}

export function buildOperatorCatalog(bundledCatalog) {
  const models = modelEntries(bundledCatalog);
  const candidates = models.filter(model => model.slug === POLICY_MODEL);
  if (candidates.length !== 1) reject('bundled_luna_missing_or_duplicated');
  const original = candidates[0];
  if (OVERRIDDEN_MODEL_FIELDS.some(key => !Object.hasOwn(original, key))
      || !Array.isArray(original.experimental_supported_tools)
      || original.experimental_supported_tools.some(tool => typeof tool !== 'string')
      || original.tool_mode !== 'code_mode_only'
      || original.shell_type !== 'unified_exec'
      || original.use_responses_lite !== true) reject('bundled_luna_metadata_drift');
  const model = structuredClone(original);
  model.apply_patch_tool_type = null;
  model.experimental_supported_tools = [];
  model.tool_mode = 'direct';
  const changed = Object.keys(original).filter(key => !isDeepStrictEqual(original[key], model[key])).sort();
  if (!isDeepStrictEqual(changed, [...OVERRIDDEN_MODEL_FIELDS].sort())) reject('bundled_luna_metadata_drift');
  const catalog = structuredClone(bundledCatalog);
  catalog.models = [model];
  return catalog;
}

export function verifyOperatorReadback(bundledCatalog, readback) {
  const expected = buildOperatorCatalog(bundledCatalog);
  const actual = modelEntries(readback);
  if (actual.length !== 1 || actual[0].slug !== POLICY_MODEL
      || !isDeepStrictEqual(actual[0], expected.models[0])) reject('operator_catalog_readback_mismatch');
  return true;
}

async function regularFileContents(file, kind, maxBytes) {
  if (typeof file !== 'string' || !path.isAbsolute(file)) reject(`${kind}_path_not_absolute`);
  let stat;
  try { stat = await lstat(file); } catch { reject(`${kind}_unreadable`); }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > maxBytes) reject(`${kind}_not_regular_file`);
  try { return await readFile(file); } catch { reject(`${kind}_unreadable`); }
}

async function verifyBinary(binaryPath) {
  const contents = await regularFileContents(binaryPath, 'cli_binary', 512 * 1024 * 1024);
  if (digest(contents) !== PINNED_CLI_SHA256) reject('cli_binary_digest_mismatch');
  return PINNED_CLI_SHA256;
}

export async function verifyBinaryAndCatalog({ binaryPath, catalogPath, expectedCatalogSha256 } = {}) {
  if (typeof expectedCatalogSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(expectedCatalogSha256)) {
    reject('catalog_digest_evidence_missing');
  }
  await verifyBinary(binaryPath);
  return verifyCatalogDigest({ catalogPath, expectedCatalogSha256 });
}

export async function verifyCatalogDigest({ catalogPath, expectedCatalogSha256 } = {}) {
  if (typeof expectedCatalogSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(expectedCatalogSha256)) {
    reject('catalog_digest_evidence_missing');
  }
  const contents = await regularFileContents(catalogPath, 'operator_catalog', MAX_CATALOG_BYTES);
  if (digest(contents) !== expectedCatalogSha256) reject('operator_catalog_digest_mismatch');
  let catalog;
  try { catalog = JSON.parse(contents.toString('utf8')); } catch { reject('operator_catalog_invalid_json'); }
  const models = modelEntries(catalog);
  if (models.length !== 1 || models[0].slug !== POLICY_MODEL
      || models[0].apply_patch_tool_type !== null
      || !isDeepStrictEqual(models[0].experimental_supported_tools, [])
      || models[0].tool_mode !== 'direct') reject('operator_catalog_policy_mismatch');
  return true;
}

export function checkedMetadataResult(result) {
  if (!object(result) || result.code !== 0 || result.drained !== true
      || typeof result.stdout !== 'string' || typeof result.stderr !== 'string'
      || result.stderr.trim() !== '' || Buffer.byteLength(result.stdout) > MAX_CATALOG_BYTES) {
    reject('cli_metadata_not_verified');
  }
  return result.stdout;
}

function parseMetadata(result) {
  const output = checkedMetadataResult(result);
  try { return JSON.parse(output); } catch { reject('cli_metadata_invalid_json'); }
}

/** The host supplies a bounded, drained runner with the identical policy args/env.
 * Only a fresh local exec is admitted: no daemon, resume/fork, dynamic tools,
 * selected capability roots, remote execution environment, or MCP servers.
 * The host must verify effective features and retain its environment whitelist.
 */
export async function prepareOperatorCatalog({ binaryPath, directory, runMetadata } = {}) {
  if (typeof runMetadata !== 'function') reject('metadata_runner_missing');
  if (typeof directory !== 'string' || !path.isAbsolute(directory)) reject('catalog_directory_not_absolute');
  let stat;
  try { stat = await lstat(directory); } catch { reject('catalog_directory_unreadable'); }
  if (!stat.isDirectory() || stat.isSymbolicLink()) reject('catalog_directory_not_regular');
  await verifyBinary(binaryPath);
  const version = checkedMetadataResult(await runMetadata(['--version'])).trim();
  if (version !== PINNED_CLI_VERSION) reject('cli_version_mismatch');
  const bundled = parseMetadata(await runMetadata(['--no-daemon', 'debug', 'models', '--bundled']));
  const catalog = buildOperatorCatalog(bundled);
  const catalogPath = path.join(directory, 'luna-direct-operator-catalog.json');
  const contents = JSON.stringify(catalog, null, 2) + '\n';
  if (Buffer.byteLength(contents) > MAX_CATALOG_BYTES) reject('operator_catalog_too_large');
  try { await writeFile(catalogPath, contents, { flag: 'wx', mode: 0o600 }); }
  catch { reject('operator_catalog_write_failed'); }
  const catalogSha256 = digest(contents);
  const readback = parseMetadata(await runMetadata(['--no-daemon', 'debug', 'models',
    '-c', `model_catalog_json=${JSON.stringify(catalogPath)}`]));
  verifyOperatorReadback(bundled, readback);
  await verifyBinaryAndCatalog({ binaryPath, catalogPath, expectedCatalogSha256: catalogSha256 });
  // Metadata proves the three model overrides, but no CLI metadata command has
  // proved an empty *exec* MCP configuration. An empty mcp_servers table is
  // recursively merged and does not clear System/cloud/managed definitions.
  // Do not turn this into true until that independent source/config gap closes.
  if (!TOOL_POLICY_SOURCE.sourceClosureVerified) reject('native_config_closure_not_verified');
  return { verified: true,
    executableSha256: PINNED_CLI_SHA256, model: POLICY_MODEL,
    catalogPath, catalogSha256,
    evidence: { sourceClosure: TOOL_POLICY_SOURCE.sourceClosure,
      sourceCommit: SOURCE_COMMIT, sourceTag: TOOL_POLICY_SOURCE.tag,
      sourceClosureVerified: TOOL_POLICY_SOURCE.sourceClosureVerified,
      nativeConfigClosureVerified: false,
      remainingBlocker: TOOL_POLICY_SOURCE.remainingBlocker,
      unifiedExecIsBackendOnly: true,
      basis: 'pinned executable plus official source closure and startup model metadata readback',
      inferenceCalls: 0, capturedInferenceToolRegistry: false,
      modelCount: 1, modelToolMode: 'direct', applyPatchToolType: null,
      experimentalSupportedTools: [], preservedOtherModelFields: true,
      requiredInvocation: 'fresh local exec with complete fixed isolation args and whitelisted child environment' } };
}
