import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { spawnSync, spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { createHash, randomUUID } from 'node:crypto';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {
  MODEL, CLI_VERSION, CLI_SHA256, MAX_BODY, TRUSTED_PROXY, DISABLED_FEATURES, BridgeError, parseTrustedToken,
  buildChildEnvironment, isolationArguments, fixedCodexArguments, verifyFeatureOutput, validateRequest,
  decodeCodexJsonl, createBridgeServer, CodexTextRunner, createWindowsJobLauncher,
} from '../codex-text-bridge.mjs';
import { TOOL_POLICY_SOURCE, EXTRA_CONFIG_OVERRIDES, EXTRA_DISABLED_FEATURES } from '../codex-tool-policy.mjs';

const TOKEN = 'ab'.repeat(32);
const MESSAGE = [{ role: 'user', content: 'Only answer this supplied text.' }];
function transcript({ usage = { input_tokens: 10, output_tokens: 3, cached_input_tokens: 2 },
  text = 'Bounded answer', suffix = [] } = {}) {
  return [
    { type: 'thread.started', thread_id: 'test-thread' },
    { type: 'turn.started' },
    { type: 'item.completed', item: { id: 'reason', type: 'reasoning', text: 'Reasoning summary' } },
    { type: 'item.completed', item: { id: 'answer', type: 'agent_message', text } },
    { type: 'turn.completed', ...(usage === undefined ? {} : { usage }) }, ...suffix,
  ].map(x => JSON.stringify(x)).join('\n');
}
const FEATURES = [...DISABLED_FEATURES.map(flag => `${flag} stable false`),
  'skip_host_skill_discovery experimental true'].join('\n');
const childResult = (stdout, code = 0) => ({ stdout, stderr: '', code, drained: true });

test('only the independent trusted hex token is parsed; duplicates and interpolation fail', () => {
  assert.equal(parseTrustedToken(`OPENAI_API_KEY=not-read-or-used\nASHARE_CODEX_BRIDGE_TOKEN=${TOKEN}\n`), TOKEN);
  for (const input of ['', `ASHARE_CODEX_BRIDGE_TOKEN=short`,
    `ASHARE_CODEX_BRIDGE_TOKEN=\${OPENAI_API_KEY}`,
    `ASHARE_CODEX_BRIDGE_TOKEN=${TOKEN}\nASHARE_CODEX_BRIDGE_TOKEN=${TOKEN}`,
    `export ASHARE_CODEX_BRIDGE_TOKEN=${TOKEN}`]) {
    assert.throws(() => parseTrustedToken(input), BridgeError);
  }
});

test('child environment removes every provider/API override and bridge credential', () => {
  const source = { SystemRoot: 'C:\\Windows', PATH: 'trusted-os-path', USERPROFILE: 'local-user',
    OPENAI_API_KEY: 'forbidden', CODEX_API_KEY: 'forbidden', CODEX_ACCESS_TOKEN: 'forbidden',
    OPENAI_BASE_URL: 'forbidden', ASHARE_CODEX_BRIDGE_TOKEN: TOKEN, ASHARE_API_TOKEN: 'forbidden',
    PYTHONPATH: 'forbidden', NODE_OPTIONS: 'forbidden', CODEX_HOME: 'untrusted-override',
    OPENAI_IDENTITY_TOKEN_FILE: 'forbidden', AWS_PROFILE: 'forbidden', HTTP_PROXY: 'forbidden',
    HTTPS_PROXY: 'https://untrusted.invalid', ALL_PROXY: 'socks5://untrusted.invalid', NO_PROXY: '*' };
  assert.deepEqual(buildChildEnvironment(source, 'trusted-auth-root'), {
    SystemRoot: 'C:\\Windows', PATH: 'trusted-os-path', USERPROFILE: 'local-user', CODEX_HOME: 'trusted-auth-root',
    HTTP_PROXY: 'http://127.0.0.1:7897', HTTPS_PROXY: 'http://127.0.0.1:7897',
    NO_PROXY: 'localhost,127.0.0.1,::1',
  });
  assert.equal(TRUSTED_PROXY, 'http://127.0.0.1:7897');
});

test('exec argv pins model, ChatGPT auth and tool policy, never accepts a client command', () => {
  const args = fixedCodexArguments('empty-directory');
  for (const flag of ['--no-daemon', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--strict-config']) {
    assert.ok(args.includes(flag));
  }
  assert.equal(args[args.indexOf('--model') + 1], MODEL);
  assert.equal(args[args.indexOf('--sandbox') + 1], 'read-only');
  assert.equal(args.at(-1), '-');
  assert.ok(args.includes('forced_login_method="chatgpt"'));
  assert.ok(args.includes('mcp_servers={}'));
  assert.ok(args.includes('web_search="disabled"'));
  assert.ok(args.includes('skills.bundled.enabled=false'));
  assert.ok(args.includes('skills.include_instructions=false'));
  assert.ok(args.includes('model_provider="openai"'));
  assert.ok(!args.some(arg => arg.startsWith('model_providers.')));
  for (const override of EXTRA_CONFIG_OVERRIDES) assert.ok(args.includes(override));
  for (const feature of EXTRA_DISABLED_FEATURES) assert.ok(DISABLED_FEATURES.includes(feature));
  for (const flag of DISABLED_FEATURES) assert.ok(args.some((arg, i) => arg === '--disable' && args[i + 1] === flag));
  verifyFeatureOutput(FEATURES);
  assert.throws(() => verifyFeatureOutput(FEATURES.replace('shell_tool stable false', 'shell_tool stable true')), /cli_isolation/);
  assert.throws(() => verifyFeatureOutput(FEATURES.replace(/^hooks.*\n/m, '')), /cli_isolation/);
});

test('unified execution backend is allowed only under the exact independently verified closure', () => {
  const actualBackend = FEATURES.replace('unified_exec stable false', 'unified_exec stable true');
  const proof = { verified: true, executableSha256: CLI_SHA256, model: MODEL,
    evidence: { sourceClosure: TOOL_POLICY_SOURCE.sourceClosure, sourceCommit: TOOL_POLICY_SOURCE.commit,
      unifiedExecIsBackendOnly: true, nativeConfigClosureVerified: true } };
  assert.throws(() => verifyFeatureOutput(actualBackend), /cli_isolation/);
  assert.doesNotThrow(() => verifyFeatureOutput(actualBackend, proof));
  for (const altered of [
    { ...proof, executableSha256: '0'.repeat(64) },
    { ...proof, evidence: { ...proof.evidence, nativeConfigClosureVerified: false } },
    { ...proof, evidence: { ...proof.evidence, sourceCommit: 'different-source' } },
  ]) assert.throws(() => verifyFeatureOutput(actualBackend, altered), /cli_isolation/);
  assert.throws(() => verifyFeatureOutput(actualBackend.replace('shell_tool stable false', 'shell_tool stable true'), proof), /cli_isolation/);
});

test('request rejects alternative models, tools, paths and oversized messages', () => {
  assert.deepEqual(validateRequest({ model: MODEL, messages: MESSAGE, temperature: 0.3, stream: true }), { messages: MESSAGE, stream: true });
  for (const value of [
    { model: 'gpt-6-luna-alternative', messages: MESSAGE },
    { model: MODEL, messages: MESSAGE, command: 'anything' },
    { model: MODEL, messages: MESSAGE, tools: [] },
    { model: MODEL, messages: MESSAGE, proxy: 'http://untrusted.invalid' },
    { model: MODEL, messages: [{ role: 'user', content: '', tool_calls: [] }] },
    { model: MODEL, messages: [{ role: 'tool', content: 'not supported' }] },
    { model: MODEL, messages: MESSAGE, stream: 'true' },
    { model: MODEL, messages: [{ role: 'user', content: 'x'.repeat(MAX_BODY) }] },
  ]) assert.throws(() => validateRequest(value), BridgeError);
});

test('typed single terminal accepts answer and observed usage; missing usage stays unknown', () => {
  assert.deepEqual(decodeCodexJsonl(transcript(), 0), { text: 'Bounded answer', usage: {
    prompt_tokens: 10, completion_tokens: 3, total_tokens: 13, prompt_tokens_details: { cached_tokens: 2 },
  } });
  const events = transcript().split('\n').map(JSON.parse);
  delete events.at(-1).usage;
  assert.deepEqual(decodeCodexJsonl(events.map(JSON.stringify).join('\n'), 0), { text: 'Bounded answer' });
});

test('known CLI cache-write and reasoning usage is preserved without inventing omitted fields', () => {
  const result = decodeCodexJsonl(transcript({ usage: { input_tokens: 8102,
    cached_input_tokens: 6912, cache_write_input_tokens: 0, output_tokens: 8, reasoning_output_tokens: 0 } }), 0);
  assert.deepEqual(result.usage, { prompt_tokens: 8102, completion_tokens: 8, total_tokens: 8110,
    prompt_tokens_details: { cached_tokens: 6912 }, cache_write_input_tokens: 0,
    completion_tokens_details: { reasoning_tokens: 0 } });
  const sparse = decodeCodexJsonl(transcript({ usage: { input_tokens: 8, output_tokens: 3,
    cache_write_input_tokens: 2, reasoning_output_tokens: 1 } }), 0);
  assert.deepEqual(sparse.usage, { prompt_tokens: 8, completion_tokens: 3, total_tokens: 11,
    cache_write_input_tokens: 2, completion_tokens_details: { reasoning_tokens: 1 } });
  assert.equal(Object.hasOwn(sparse.usage, 'prompt_tokens_details'), false);
  assert.equal(Object.hasOwn(decodeCodexJsonl(transcript(), 0).usage, 'cache_write_input_tokens'), false);
});

test('usage rejects unknown names, nonintegers and component counts beyond observed totals', () => {
  const invalid = [
    { input_tokens: 8, output_tokens: 3, cache_write_input_tokens: -1 },
    { input_tokens: 8, output_tokens: 3, cache_write_input_tokens: true },
    { input_tokens: 8, output_tokens: 3, cached_input_tokens: 6, cache_write_input_tokens: 3 },
    { input_tokens: 8, output_tokens: 3, reasoning_output_tokens: 4 },
    { input_tokens: 8, output_tokens: 3, reasoning_output_tokens: 0.5 },
    { input_tokens: 8, output_tokens: 3, cache_creation_input_tokens: 0 },
    { input_tokens: 8, output_tokens: 3, unknown_tokens: 0 },
  ];
  for (const usage of invalid) assert.throws(() => decodeCodexJsonl(transcript({ usage }), 0), /cli_invalid_usage/);
});

test('new usage fields do not permit unknown startup errors before the sole turn', () => {
  const events = transcript({ usage: { input_tokens: 8102, cached_input_tokens: 6912,
    cache_write_input_tokens: 0, output_tokens: 8, reasoning_output_tokens: 0 } }).split('\n').map(JSON.parse);
  events.splice(1, 0,
    { type: 'item.completed', item: { id: 'startup-1', type: 'error', message: 'unknown startup diagnostic' } },
    { type: 'item.completed', item: { id: 'startup-2', type: 'error', message: 'another unknown diagnostic' } });
  assert.throws(() => decodeCodexJsonl(events.map(JSON.stringify).join('\n'), 0), error => {
    assert.equal(error.code, 'cli_event_order');
    assert.equal(error.usage.prompt_tokens, 8102);
    assert.equal(error.usage.completion_tokens, 8);
    return true;
  });
  for (const text of [events.map(JSON.stringify).join('\n') + '\ninvalid-json',
    events.slice(0, -1).map(JSON.stringify).join('\n'),
    events.map(JSON.stringify).join('\n') + '\n' + JSON.stringify({ type: 'turn.started' })]) {
    assert.throws(() => decodeCodexJsonl(text, 0), error => { assert.equal(error.usage, undefined); return true; });
  }
});

test('malformed, truncated, multiple-turn, tool and failed CLI events never become answers', () => {
  const events = transcript().split('\n').map(JSON.parse);
  const cases = [
    transcript() + '\nnot-json',
    events.slice(0, -1).map(JSON.stringify).join('\n'),
    transcript({ suffix: [{ type: 'turn.started' }] }),
    transcript().replace('"agent_message"', '"command_execution"'),
    transcript().replace('"turn.completed"', '"turn.failed"'),
    transcript({ usage: { input_tokens: 1, output_tokens: -1 } }),
    transcript({ usage: { input_tokens: true, output_tokens: 0 } }),
    transcript({ usage: { input_tokens: 1, output_tokens: 0, cached_input_tokens: 2 } }),
    transcript().replace('"turn.completed"', '"error"'),
    transcript().replace('"type":"turn.completed"', '"type":"turn.failed","type":"turn.completed"'),
    transcript().replace('"type":"turn.completed"', '"type":"turn.completed","error":"hidden"'),
    transcript().replace('"type":"item.completed"', '"type":"item.updated"'),
  ];
  for (const value of cases) assert.throws(() => decodeCodexJsonl(value, 0), BridgeError);
  assert.throws(() => decodeCodexJsonl(transcript(), 1), /cli_process_failed/);
});

async function testCatalogProof({ cwd }) {
  const catalogPath = path.join(cwd, 'operator-catalog.json');
  const content = JSON.stringify({ models: [{ slug: MODEL, tools: [] }] });
  await writeFile(catalogPath, content);
  return { verified: true, model: MODEL, executableSha256: CLI_SHA256, catalogPath,
    catalogSha256: createHash('sha256').update(content).digest('hex'),
    evidence: { nativeConfigClosureVerified: true, sourceClosure: TOOL_POLICY_SOURCE.sourceClosure,
      sourceCommit: TOOL_POLICY_SOURCE.commit, unifiedExecIsBackendOnly: true } };
}
function mockRunner(execute, verifyToolCatalog = testCatalogProof, readExecutableDigest = async () => CLI_SHA256) {
  return new CodexTextRunner({ executable: path.resolve('trusted-codex.exe'), env: {},
    execute, verifyToolCatalog, readExecutableDigest });
}
function preflightResult(args) {
  if (args[0] === '--version') return childResult(CLI_VERSION);
  if (args[0] === 'features') return childResult(FEATURES);
  if (args[0] === 'login') return childResult('Logged in using ChatGPT');
  return undefined;
}

test('preflight fails closed without independently verified native tool catalog', async () => {
  const runner = mockRunner(async (_, args) => preflightResult(args), async () => false);
  await assert.rejects(runner.preflight(), /native_tool_catalog_not_verified/);
  await assert.rejects(runner.run(MESSAGE), /preflight_required/);
});

test('API-key login never launches inference or falls back, including changed login after preflight', async () => {
  let changed = false; let inference = 0;
  const runner = mockRunner(async (_, args) => {
    if (args[0] === 'login' && changed) return childResult('Logged in using an API key');
    const preflight = preflightResult(args);
    if (preflight) return preflight;
    inference += 1;
    return childResult(transcript());
  });
  await runner.preflight();
  changed = true;
  await assert.rejects(runner.run(MESSAGE), /chatgpt_login_required/);
  assert.equal(inference, 0);
});

test('failed second preflight clears ready; each run rechecks binary, flags and catalog evidence', async () => {
  let badFlags = false; let digest = CLI_SHA256; let inference = 0; let checks = 0;
  const runner = mockRunner(async (_, args) => {
    if (args[0] === 'features' && badFlags) return childResult(FEATURES.replace('shell_tool stable false', 'shell_tool stable true'));
    const result = preflightResult(args); if (result) return result;
    inference += 1; return childResult(transcript());
  }, async context => { checks += 1; return testCatalogProof(context); }, async () => digest);
  await runner.preflight(); assert.equal(runner.ready, true);
  badFlags = true;
  await assert.rejects(runner.preflight(), /cli_isolation_not_verified/);
  assert.equal(runner.ready, false);
  await assert.rejects(runner.run(MESSAGE), /preflight_required/);
  badFlags = false; await runner.preflight();
  digest = '0'.repeat(64);
  await assert.rejects(runner.run(MESSAGE), /cli_binary_changed/);
  assert.equal(runner.ready, false); assert.equal(inference, 0);
  digest = CLI_SHA256; await runner.preflight();
  await runner.run(MESSAGE);
  assert.equal(inference, 1); assert.equal(checks, 5);
});

test('permanent true or digest-drifted catalog evidence cannot admit the runner', async () => {
  const fake = async (_, args) => preflightResult(args);
  await assert.rejects(mockRunner(fake, async () => true).preflight(), /native_tool_catalog_not_verified/);
  await assert.rejects(mockRunner(fake, async context => ({ ...(await testCatalogProof(context)),
    catalogSha256: '0'.repeat(64) })).preflight(), /native_tool_catalog_changed/);
  const unclosed = mockRunner(fake, async context => ({ ...(await testCatalogProof(context)),
    evidence: { nativeConfigClosureVerified: false } }));
  await assert.rejects(unclosed.preflight(), /native_config_closure_not_verified/);
  assert.equal(unclosed.ready, false);
});

test('timeout has no retry, uses stdin transcript, and drains before caller completion', async () => {
  let launches = 0; let input;
  const runner = mockRunner(async (_, args, options) => {
    const preflight = preflightResult(args);
    if (preflight) return preflight;
    launches += 1; input = options.input;
    assert.ok(!args.some(arg => arg.includes(MESSAGE[0].content)));
    throw new BridgeError('cli_timeout', 504, true);
  });
  await runner.preflight();
  await assert.rejects(runner.run(MESSAGE), /cli_timeout/);
  assert.equal(launches, 1);
  assert.ok(input.endsWith(JSON.stringify({ version: 1, messages: MESSAGE })));
});

async function withServer(run, fn, options = {}) {
  const server = createBridgeServer({ token: TOKEN, run, ...options });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const origin = `http://127.0.0.1:${server.address().port}`;
  try { await fn(origin, server); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}
const body = overrides => JSON.stringify({ model: MODEL, messages: MESSAGE, ...overrides });
function request(origin, overrides = {}, headers = {}) {
  return fetch(origin + '/v1/chat/completions', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}`, ...headers }, body: body(overrides) });
}

test('HTTP authentication/body bounds reject before any CLI invocation', async () => {
  let calls = 0;
  await withServer(async () => { calls += 1; return { text: 'ok', drained: true }; }, async origin => {
    assert.equal((await request(origin, {}, { Authorization: 'Bearer wrong' })).status, 401);
    assert.equal((await request(origin, { model: 'other' })).status, 400);
    assert.equal((await request(origin, {}, { Origin: 'http://untrusted.test' })).status, 403);
    const response = await fetch(origin + '/v1/chat/completions', { method: 'POST', headers: {
      Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json',
    }, body: 'x'.repeat(MAX_BODY + 1) });
    assert.equal(response.status, 413);
    assert.equal(calls, 0);
  });
});

test('buffered SSE sends only verified complete answer and honestly identifies buffering', async () => {
  await withServer(async () => ({ text: 'verified answer', drained: true }), async origin => {
    const response = await request(origin, { stream: true });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('x-ashare-streaming-mode'), 'buffered-verified-answer');
    const lines = (await response.text()).split('\n').filter(x => x.startsWith('data: '));
    assert.equal(lines.length, 3);
    assert.equal(JSON.parse(lines[0].slice(6)).choices[0].delta.content, 'verified answer');
    assert.equal(JSON.parse(lines[1].slice(6)).choices[0].finish_reason, 'stop');
    assert.equal(lines[2], 'data: [DONE]');
  });
});

test('failed or undrained CLI sends an explicit SSE error, never an empty answer or released slot', async () => {
  let calls = 0;
  await withServer(async () => { calls += 1; throw new BridgeError('cli_tree_not_drained', 503, false); }, async (origin, server) => {
    const response = await request(origin, { stream: true });
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /text\/event-stream/);
    const text = await response.text();
    assert.match(text, /"code":"cli_tree_not_drained"/);
    assert.doesNotMatch(text, /"content"/);
    assert.deepEqual(server.bridgeState(), { active: 1, blocked: true });
    assert.equal((await request(origin)).status, 503);
    assert.equal(calls, 1);
  });
});

test('SSE headers/comment arrive before completion; failed answer preserves only verified terminal usage', async () => {
  let complete;
  await withServer(async () => new Promise((resolve, reject) => {
    complete = () => { const error = new BridgeError('cli_event_order'); error.usage = { prompt_tokens: 12, completion_tokens: 2, total_tokens: 14 }; reject(error); };
  }), async origin => {
    const response = await request(origin, { stream: true });
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    assert.match(new TextDecoder().decode((await reader.read()).value), /^: buffered_verified_answer/);
    assert.equal(typeof complete, 'function');
    complete();
    let rest = '';
    while (true) { const part = await reader.read(); if (part.done) break; rest += new TextDecoder().decode(part.value); }
    assert.match(rest, /"error":/); assert.match(rest, /"prompt_tokens":12/);
    assert.doesNotMatch(rest, /"content":/);
  });
});

test('client disconnect keeps slot until the cancelled transport proves drain', async () => {
  let observeAbort; const aborted = new Promise(resolve => { observeAbort = resolve; });
  let finishDrain;
  await withServer((_, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => {
      observeAbort(); finishDrain = () => reject(new BridgeError('client_disconnected', 499, true));
    }, { once: true });
  }), async (origin, server) => {
    const client = http.request(origin + '/v1/chat/completions', { method: 'POST', headers: {
      Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json',
    } });
    client.on('error', () => {});
    client.end(body({}));
    while (server.bridgeState().active === 0) await new Promise(resolve => setTimeout(resolve, 5));
    client.destroy();
    await aborted;
    assert.equal(server.bridgeState().active, 1);
    assert.equal((await request(origin)).status, 503);
    finishDrain();
    while (server.bridgeState().active !== 0) await new Promise(resolve => setTimeout(resolve, 5));
    assert.deepEqual(server.bridgeState(), { active: 0, blocked: false });
  });
});

test('normal shutdown stops accepting and waits until its aborted request drains', async () => {
  let finish;
  await withServer((_, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => { finish = () => reject(new BridgeError('client_disconnected', 499)); }, { once: true });
  }), async (origin, server) => {
    const pending = request(origin, { stream: true });
    const response = await pending; let stopped = false;
    const stopping = server.bridgeShutdown().then(() => { stopped = true; });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(stopped, false); assert.equal(server.bridgeState().active, 1);
    finish(); await response.text(); await stopping;
    assert.equal(stopped, true); assert.equal(server.bridgeState().active, 0);
  });
});

test('hung guardian plus failed cancel helper returns undrained within a bound and poisons the slot', async () => {
  let guardian; let guardianKills = 0;
  const fakeSpawn = (_, args, options) => {
    const child = new EventEmitter(); child.stdin = new PassThrough();
    child.kill = () => { if (child === guardian) guardianKills += 1; return true; };
    if (options.stdio[1] === 'pipe') {
      guardian = child; child.stdout = new PassThrough(); child.stderr = new PassThrough();
    } else queueMicrotask(() => child.emit('close', 1));
    return child;
  };
  const launch = createWindowsJobLauncher({ spawnProcess: fakeSpawn, platform: 'win32', drainGraceMs: 20, spawnOverheadMs: 0 });
  const began = Date.now();
  await withServer(async () => launch('fake.exe', [], { cwd: os.tmpdir(), env: {}, input: '', timeoutMs: 1, maxOutput: 256 }),
    async (origin, server) => {
      const response = await request(origin); assert.equal(response.status, 503);
      assert.equal((await response.json()).error.code, 'cli_tree_not_drained');
      assert.deepEqual(server.bridgeState(), { active: 1, blocked: true });
      assert.equal((await request(origin)).status, 503);
      assert.ok(Date.now() - began < 1000); assert.equal(guardianKills, 0);
    });
  guardian.emit('close', 1);
});

test('unassigned native-child termination failure cannot be relabelled as an empty drained Job', async () => {
  let helpers = 0;
  const launch = createWindowsJobLauncher({ platform: 'win32', spawnProcess: (_, args, options) => {
    const child = new EventEmitter(); child.stdin = new PassThrough(); child.kill = () => true;
    if (options.stdio[1] === 'pipe') {
      child.stdout = new PassThrough(); child.stderr = new PassThrough();
      queueMicrotask(() => child.emit('close', 98));
    } else { helpers += 1; queueMicrotask(() => child.emit('close', 0)); }
    return child;
  } });
  await assert.rejects(launch('fake.exe', [], { cwd: os.tmpdir(), env: {}, timeoutMs: 1000, maxOutput: 128 }),
    error => error.code === 'cli_tree_not_drained' && error.drained === false);
  assert.equal(helpers, 0);
});

async function bounded(promise, ms = 15000) {
  let timer;
  try { return await Promise.race([promise, new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(new Error('Synthetic child fixture deadline')), ms);
  })]); } finally { clearTimeout(timer); }
}
async function waitForPids(file) {
  for (let i = 0; i < 500; i += 1) {
    try { return JSON.parse(await readFile(file, 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error; }
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('Synthetic child fixture did not publish its pids');
}
async function assertPidsGone(pids) {
  for (let i = 0; i < 500; i += 1) {
    const alive = pids.some(pid => { try { process.kill(pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') return false; throw error; } });
    if (!alive) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail('Owned synthetic descendants survived owner shutdown');
}

test('real Windows bridge owner stop/crash drains descendants; restart never resumes the old request',
  { skip: process.platform !== 'win32', timeout: 90000 }, async () => {
    const cwd = await mkdtemp(path.join(os.tmpdir(), 'ashare-owner-lifecycle-'));
    const moduleUrl = new URL('../codex-text-bridge.mjs', import.meta.url).href;
    const lease = `Local\\AShareOwnerTest-${randomUUID()}`;
    const childCode = 'const c=require("node:child_process").spawn(process.execPath,["-e","setInterval(()=>{},1000)"],{stdio:"ignore"});require("node:fs").writeFileSync(process.argv[1],JSON.stringify([process.pid,c.pid]));setInterval(()=>{},1000)';
    const fixture = `import {createBridgeServer,createWindowsJobLauncher,buildChildEnvironment,BridgeError} from ${JSON.stringify(moduleUrl)};
      const options=JSON.parse(process.argv[1]);const launch=createWindowsJobLauncher({leaseName:options.lease});
      const server=createBridgeServer({token:'ab'.repeat(32),run:async(_,{signal})=>{const r=await launch(process.execPath,['-e',${JSON.stringify(childCode)},options.pidFile],{signal,env:buildChildEnvironment(process.env),cwd:options.cwd,input:'',timeoutMs:30000,maxOutput:4096});if(r.code!==0)throw new BridgeError('synthetic_process_rejected',503);return{text:'inert',drained:r.drained};}});
      server.listen(0,'127.0.0.1',()=>process.send({port:server.address().port}));
      process.on('message',async message=>{if(message==='shutdown'){await server.bridgeShutdown();process.disconnect();}});`;
    const owners = [];
    const startOwner = async name => {
      const pidFile = path.join(cwd, name + '.json');
      const owner = spawn(process.execPath, ['--input-type=module', '-e', fixture, JSON.stringify({ cwd, pidFile, lease })], {
        windowsHide: true, stdio: ['ignore', 'pipe', 'pipe', 'ipc'], env: buildChildEnvironment(process.env),
      });
      owners.push(owner); owner.stdout.resume(); owner.stderr.resume();
      const [{ port }] = await bounded(once(owner, 'message'));
      return { owner, pidFile, origin: `http://127.0.0.1:${port}` };
    };
    try {
      const first = await startOwner('normal');
      const response = await request(first.origin, { stream: true });
      const pids = await waitForPids(first.pidFile);
      const exited = once(first.owner, 'exit'); first.owner.send('shutdown');
      await response.text(); await bounded(exited); await assertPidsGone(pids);
      const second = await startOwner('crashed');
      const interrupted = await request(second.origin, { stream: true });
      const oldPids = await waitForPids(second.pidFile);
      const crashed = once(second.owner, 'exit'); second.owner.kill(); await bounded(crashed);
      // Reading a crashed local HTTP owner is expected to fail; it is not an answer.
      await interrupted.text().catch(() => {});
      const replacement = await startOwner('replacement');
      await assertPidsGone(oldPids);
      await assert.rejects(readFile(replacement.pidFile), error => error.code === 'ENOENT');
      const health = await fetch(replacement.origin + '/health', { headers: { Authorization: `Bearer ${TOKEN}` } });
      assert.equal((await health.json()).active, 0);
      const replaced = once(replacement.owner, 'exit'); replacement.owner.send('shutdown'); await bounded(replaced);
    } finally {
      for (const owner of owners) if (owner.exitCode === null && owner.signalCode === null) owner.kill();
      await rm(cwd, { recursive: true, force: true });
    }
  });

test('Windows Job guardian executes an inert local process; timeout drains its descendants',
  { skip: process.platform !== 'win32', timeout: 90000 }, async () => {
    const cwd = await mkdtemp(path.join(os.tmpdir(), 'ashare-bridge-job-test-'));
    try {
      const launch = createWindowsJobLauncher({ leaseName: `Local\\AShareBridgeTest-${randomUUID()}` });
      const options = { env: buildChildEnvironment(process.env), cwd, input: '', timeoutMs: 5000, maxOutput: 4096 };
      const result = await launch(process.execPath, ['-e', 'process.stdout.write("inert")'], options);
      assert.equal(result.code, 0);
      assert.equal(result.stdout, 'inert');
      assert.equal(result.drained, true);
      const unicode = await launch(process.execPath, ['-e', 'process.stdin.pipe(process.stdout)'], {
        ...options, input: '中文输入必须完整 😀',
      });
      assert.equal(unicode.stdout, '中文输入必须完整 😀');
      // The independent pid artifact is synthetic test evidence, never CLI/user content.
      const pidPath = path.join(cwd, 'descendant-pid.txt');
      const childScript = 'const child=require("node:child_process").spawn(process.execPath,["-e","setInterval(()=>{},1000)"],{stdio:"ignore"});require("node:fs").writeFileSync(process.argv[1],String(child.pid));setInterval(()=>{},1000)';
      await assert.rejects(launch(process.execPath, ['-e', childScript, pidPath], { ...options, timeoutMs: 500 }),
        error => error.code === 'cli_timeout' && error.drained === true);
      const descendant = Number(await readFile(pidPath, 'utf8'));
      assert.ok(Number.isSafeInteger(descendant) && descendant > 0);
      assert.throws(() => process.kill(descendant, 0), error => error.code === 'ESRCH');
      const abortPidPath = path.join(cwd, 'aborted-descendant-pid.txt');
      const aborter = new AbortController();
      const abortedRun = launch(process.execPath, ['-e', childScript, abortPidPath], {
        ...options, signal: aborter.signal, timeoutMs: 10000,
      });
      // Install the rejection handler before aborting to avoid an unhandled rejection.
      const abortedAssertion = assert.rejects(abortedRun,
        error => error.code === 'client_disconnected' && error.drained === true);
      let abortPid;
      for (let attempt = 0; attempt < 150 && !abortPid; attempt += 1) {
        try { abortPid = Number(await readFile(abortPidPath, 'utf8')); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
        if (!abortPid) await new Promise(resolve => setTimeout(resolve, 20));
      }
      assert.ok(Number.isSafeInteger(abortPid) && abortPid > 0);
      aborter.abort();
      await abortedAssertion;
      assert.throws(() => process.kill(abortPid, 0), error => error.code === 'ESRCH');
    } finally { await rm(cwd, { recursive: true, force: true }); }
  });

test('installed Windows CLI accepts isolation config without inference; unsafe reported flags still block',
  { skip: process.platform !== 'win32', timeout: 60000 }, async context => {
    const env = buildChildEnvironment(process.env);
    const windowsRoot = env.SystemRoot ?? env.SYSTEMROOT ?? 'C:\\Windows';
    const located = spawnSync(path.join(windowsRoot, 'System32', 'where.exe'), ['codex.exe'], {
      env, encoding: 'utf8', windowsHide: true, timeout: 5000,
    });
    const executable = (located.stdout ?? '').split(/\r?\n/).find(p => path.isAbsolute(p) && p.toLowerCase().endsWith('codex.exe'));
    if (located.status !== 0 || !executable) { context.skip('No installed Codex CLI; no inference attempted'); return; }
    const cwd = await mkdtemp(path.join(os.tmpdir(), 'ashare-cli-config-test-'));
    try {
      const result = await createWindowsJobLauncher({ leaseName: `Local\\AShareBridgeTest-${randomUUID()}` })(executable,
        ['features', 'list', ...isolationArguments()], {
          env, cwd, input: '', timeoutMs: 15000, maxOutput: 1048576,
        });
      // Never include CLI diagnostics/configuration values in assertion output.
      assert.equal(result.code, 0, 'Installed CLI rejected the isolation configuration');
      assert.equal(result.drained, true);
      const rows = new Map(result.stdout.trim().split(/\r?\n/).map(line => {
        const fields = line.trim().split(/\s+/); return [fields[0], fields.at(-1)];
      }));
      const unsafe = DISABLED_FEATURES.filter(flag => rows.get(flag) !== 'false');
      if (unsafe.length || rows.get('skip_host_skill_discovery') !== 'true') {
        assert.throws(() => verifyFeatureOutput(result.stdout), /cli_isolation_not_verified/);
        context.diagnostic(`Zero inference; production remains blocked by reported flags: ${unsafe.join(',') || 'skill_discovery'}`);
      } else {
        assert.doesNotThrow(() => verifyFeatureOutput(result.stdout));
        context.diagnostic('Zero inference; feature switches verified, independent native-tool gate is still required');
      }
    } finally { await rm(cwd, { recursive: true, force: true }); }
  });
