#!/usr/bin/env node
/**
 * 全 GET 端点健康巡检。
 *
 * 从 /openapi.json 拿权威清单（不会漏也不会多），自动替换路径参数、
 * 填必需查询参数，逐个打真实后端，输出非 2xx 的端点及错误摘要。
 * 只读：不触碰任何写接口（POST/PUT/DELETE）。
 *
 * 用法：node scripts/api-sweep.js [baseUrl]
 */
const BASE = process.argv[2] || "http://127.0.0.1:8000";

// 路径参数替换值（用真实存在的数据，才能验出真问题）
const PATH_VALUES = {
  symbol: "600519",
  trade_date: "20260828",
  date: "2026-08-28",
  target_date: "20260828",
  id: "1",
  rule_id: "1",
  period: "day",
  group: "",
  ts: "2026-08-28",
  version: "v1",
  name: "default",
  code: "600519",
  strategy_id: "ma_cross",
};

// 查询参数默认值：只给**必需**参数填，选填的一律不带（走后端默认）
const QUERY_VALUES = {
  symbol: "600519",
  symbols: "600519,000001",
  q: "茅台",
  limit: "5",
  date: "2026-08-28",
  trade_date: "20260828",
  target_date: "20260828",
  period: "day",
  bars: "120",
  periods: "4",
  min_count: "2",
  min_boards: "1",
  days: "10",
  strategy_id: "ma_cross",
  rule_id: "1",
  acknowledged: "false",
  changeLow: "-10",
  changeHigh: "10",
  minAmountYi: "0",
  minTurnover: "0",
  excludeST: "true",
  excludeBJ: "true",
  excludeNew: "false",
  top: "20",
};

async function main() {
  const spec = await (await fetch(`${BASE}/openapi.json`)).json();
  const targets = [];

  for (const [path, ops] of Object.entries(spec.paths)) {
    const get = ops.get;
    if (!get) continue;

    // 路径参数：取不到替换值就跳过（宁可不测，也不用假数据编造结果）
    let resolved = path;
    let skip = false;
    for (const m of path.matchAll(/\{(\w+)\}/g)) {
      const key = m[1];
      const val = PATH_VALUES[key];
      if (val === undefined || val === "") { skip = true; break; }
      resolved = resolved.replace(`{${key}}`, encodeURIComponent(val));
    }
    if (skip) { targets.push({ path, url: null, note: `缺路径参数默认值: ${path}` }); continue; }

    const qs = new URLSearchParams();
    for (const p of get.parameters || []) {
      if (p.in !== "query") continue;
      if (!p.required) continue;
      const val = QUERY_VALUES[p.name] ?? p.schema?.default ?? p.schema?.enum?.[0];
      if (val === undefined) { qs.append(p.name, ""); }
      else qs.append(p.name, String(val));
    }
    const suffix = qs.toString() ? `?${qs}` : "";
    targets.push({ path, url: `${BASE}${resolved}${suffix}` });
  }

  const results = [];
  for (const t of targets) {
    if (!t.url) { results.push({ ...t, status: 0, note: t.note }); continue; }
    const t0 = Date.now();
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 45000);
      const r = await fetch(t.url, { signal: ctrl.signal, headers: { "Accept": "application/json" } });
      clearTimeout(timer);
      const body = await r.text();
      let note = "";
      if (!r.ok) note = body.slice(0, 160).replace(/\s+/g, " ");
      results.push({ ...t, status: r.status, ms: Date.now() - t0, note });
    } catch (e) {
      results.push({ ...t, status: -1, ms: Date.now() - t0, note: e.message });
    }
  }

  const ok = results.filter((r) => r.status >= 200 && r.status < 300);
  const bad = results.filter((r) => !(r.status >= 200 && r.status < 300));

  console.log(`\n扫描 ${results.length} 个 GET 端点（base=${BASE}）`);
  console.log(`通过 ${ok.length} / 异常 ${bad.length}\n`);

  if (bad.length) {
    console.log("=== 异常明细 ===");
    for (const r of bad) {
      const st = r.status === -1 ? "ERR " : String(r.status);
      console.log(`[${st}] ${r.path}${r.ms ? ` (${r.ms}ms)` : ""}`);
      if (r.note) console.log(`        ${r.note}`);
    }
  }

  const slow = ok.filter((r) => r.ms > 5000).sort((a, b) => b.ms - a.ms);
  if (slow.length) {
    console.log("\n=== 慢端点 (>5s) ===");
    for (const r of slow) console.log(`${String(r.ms).padStart(6)}ms  ${r.path}`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
