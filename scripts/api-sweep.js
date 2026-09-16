#!/usr/bin/env node
/**
 * 全 GET 端点健康巡检。
 *
 * 从 /openapi.json 拿权威清单（不会漏也不会多），自动替换路径参数、
 * 填必需查询参数，逐个打真实后端，输出非 2xx 的端点及错误摘要。
 * 只读：不触碰任何写接口（POST/PUT/DELETE）。
 *
 * 用法：node scripts/api-sweep.js [baseUrl]
 *
 * ⚠️ R22（2026-09-15）起后端为**默认拒绝**鉴权：配了 ASHARE_API_TOKEN 后**所有**路由
 * 都要凭据，唯一豁免 GET /api/health。故本脚本会自动带上 X-API-Token，取值顺序：
 *   ① 环境变量 ASHARE_API_TOKEN
 *   ② backend/.env 的 ASHARE_API_TOKEN 行
 *   ③ 都取不到 ⇒ 不带头（本地 local 姿态下正确）
 * 不带凭据跑出来的「满屏 401」是缺凭据，不是数据问题——脚本会显式提示。
 */
const { readFileSync } = require("node:fs");
const { dirname, join } = require("node:path");

const BASE = process.argv[2] || "http://127.0.0.1:8000";

/** 取巡检凭据（只读、不打印明文）。取不到返回空串 = 不带头。 */
function resolveToken() {
  if (process.env.ASHARE_API_TOKEN) return process.env.ASHARE_API_TOKEN.trim();
  try {
    const text = readFileSync(join(__dirname, "..", "backend", ".env"), "utf8");
    const m = text.match(/^\s*ASHARE_API_TOKEN\s*=\s*(.+)$/m);
    return m ? m[1].trim().replace(/^["']|["']$/g, "") : "";
  } catch {
    return ""; // 没有 backend/.env（如 CI 检出）⇒ 不带头
  }
}
const TOKEN = resolveToken();
const AUTH_HEADERS = TOKEN ? { "X-API-Token": TOKEN } : {};

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
  // 板块代码（2026-09-10 补：此前缺省值 → /market/board-fund-flow/{board_code}/*
  // 两个下钻端点一直被跳过，属巡检盲区。BK1024=绿色电力，实测存在）
  board_code: "BK1024",
  run_id: "missing-run",
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

/**
 * 载荷体检：找出"HTTP 200 但没数据"的静默失败。
 * 这类比 500 危险——监控看不到、页面也不报错，用户只觉得"功能没了"。
 *
 * 空列表未必是缺陷（今天没有预警规则/复盘报告是合法的），
 * 所以这里只**列出并分类**，由人判定，脚本不做自动断言。
 */
function inspect(body) {
  const out = { counts: [], empties: [], stale: null };
  let j;
  try { j = JSON.parse(body); } catch { return null; }
  const data = j && typeof j === "object" && "data" in j ? j.data : j;

  const walk = (node, prefix, depth) => {
    if (depth > 2 || node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      out.counts.push(`${prefix}=${node.length}`);
      if (node.length === 0) out.empties.push(prefix);
      return;
    }
    for (const [k, v] of Object.entries(node)) {
      const p = prefix ? `${prefix}.${k}` : k;
      if (Array.isArray(v)) {
        out.counts.push(`${p}=${v.length}`);
        if (v.length === 0) out.empties.push(p);
      } else if (v && typeof v === "object") {
        walk(v, p, depth + 1);
      } else if (v === null) {
        out.empties.push(`${p}:null`);
      }
    }
  };
  walk(data, "", 0);

  const meta = j && j.meta;
  if (meta && typeof meta === "object") {
    if (meta.is_stale === true) out.stale = "is_stale=true";
    if (typeof meta.quality === "string") out.stale = `quality=${meta.quality}`;
  }
  return out;
}

async function main() {
  const spec = await (await fetch(`${BASE}/openapi.json`, { headers: AUTH_HEADERS })).json();
  const targets = [];

  // event_id 是自增序列（今天 386，明天就不是了）——写死必然过期，运行时取一个真实值
  try {
    const r = await fetch(`${BASE}/api/events?limit=1`, { headers: AUTH_HEADERS });
    const j = await r.json();
    const firstId = j?.data?.items?.[0]?.id;
    if (firstId != null) PATH_VALUES.event_id = String(firstId);
  } catch {}

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
      const r = await fetch(t.url, {
        signal: ctrl.signal,
        headers: { Accept: "application/json", ...AUTH_HEADERS },
      });
      clearTimeout(timer);
      const body = await r.text();
      let note = "";
      if (!r.ok) note = body.slice(0, 160).replace(/\s+/g, " ");
      results.push({ ...t, status: r.status, ms: Date.now() - t0, note, body: r.ok ? body : "" });
    } catch (e) {
      results.push({ ...t, status: -1, ms: Date.now() - t0, note: e.message, body: "" });
    }
  }

  const ok = results.filter((r) => r.status >= 200 && r.status < 300);
  const bad = results.filter((r) => !(r.status >= 200 && r.status < 300));

  console.log(`\n扫描 ${results.length} 个 GET 端点（base=${BASE}）`);
  console.log(`通过 ${ok.length} / 异常 ${bad.length}\n`);

  // R22：401 满屏几乎必然是「后端配了 token 而巡检没带凭据」——先提示再往下看明细，
  // 避免把鉴权缺配误读成"全站挂了"。
  const n401 = results.filter((r) => r.status === 401).length;
  if (n401 > 0) {
    console.log(
      `⚠️  ${n401} 个端点返回 401。` +
        (TOKEN
          ? "已带凭据仍 401 ⇒ 凭据与后端不一致（或后端 auth_mode=shared 配了另一个值）。\n"
          : "本次**未带凭据** ⇒ 后端已启用 R22 默认拒绝鉴权。设置 ASHARE_API_TOKEN" +
            "（或让 backend/.env 可读）后重跑；这不是数据问题。\n")
    );
  }

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

  // ---- 载荷体检：空数据 / 过期数据
  console.log("\n=== 200 但数据为空（需人工判定是否真实原因）===");
  let emptyN = 0;
  for (const r of ok) {
    const ins = inspect(r.body);
    if (!ins) continue;
    if (ins.empties.length) {
      emptyN++;
      console.log(`${r.path}\n        空: ${ins.empties.join(", ")}`);
    }
  }
  if (!emptyN) console.log("（无）");

  console.log("\n=== 200 但数据过期/降级 ===");
  let staleN = 0;
  for (const r of ok) {
    const ins = inspect(r.body);
    if (ins && ins.stale) { staleN++; console.log(`${r.path}  ${ins.stale}`); }
  }
  if (!staleN) console.log("（无）");

  console.log("\n=== 各端点返回条数 ===");
  for (const r of ok) {
    const ins = inspect(r.body);
    if (ins && ins.counts.length) console.log(`${r.path.padEnd(42)} ${ins.counts.join(" ")}`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
