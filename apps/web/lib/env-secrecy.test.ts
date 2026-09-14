import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 前端凭据禁令守卫（2026-09-14 安全修复配套）。
 *
 * 背景：`lib/api.ts` 曾用 `process.env.NEXT_PUBLIC_API_TOKEN` 携带写鉴权头。
 * `NEXT_PUBLIC_*` 由 Next **构建期内联**为客户端 bundle 里的字面量 ⇒ 该 token
 * 等价于公开常量：任何访客「查看网页源码」即可取出唯一写保护凭据，它还会进入
 * 浏览器历史与反代访问日志。修复把 token 归位到服务端反向代理
 * （`app/backend/[...path]/route.ts` 读 `ASHARE_API_TOKEN` 后注入请求头）。
 *
 * 本文件的作用是**防止回潮**：只要有人再把密钥类变量加回 NEXT_PUBLIC_ 命名空间、
 * 写进 `.env*`，或把代理里的 token 注入删掉，这里立刻变红。
 */

const WEB_ROOT = path.resolve(import.meta.dirname, "..");
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "dist", "coverage", ".turbo"]);
const SCAN_EXT = new Set([".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"]);

/** 变量名里出现这些词 = 该变量承载凭据，绝不能暴露给浏览器。 */
const SECRET_NAME = /(TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_?KEY|PRIVATE_?KEY|CREDENTIAL)/i;

const NEXT_PUBLIC_ENV = /process\.env\.(NEXT_PUBLIC_[A-Za-z0-9_]*)/g;
/**
 * 模板字面量插值补扫：`` `Bearer ${process.env.X}` `` 里的 X 位于**字符串内部**，
 * 任何「字符串整体掩码」的骨架扫描都看不到它 ⇒ 单列一条定点正则补这个缺口。
 * （当前仓库实测 0 处，此条为前瞻性防御，不是已知缺陷的回填。）
 */
const NEXT_PUBLIC_ENV_IN_TEMPLATE = /\$\{[^}]*process\.env\.(NEXT_PUBLIC_[A-Za-z0-9_]*)/g;

/**
 * 扫描面 = **可能进入客户端 bundle 的生产源码**。
 *
 * - 排除 `*.test.*` / `*.spec.*` / `__tests__/`：它们不会被打包，且本守卫的
 *   自证用例必须以字符串形式写出样本变量名（否则守卫自伤）。
 * - 刻意「扫全量再排除」而不是「只扫 app/ + components/」：后者是另一种
 *   「扫描面收窄 ⇒ 输出与通过完全一样」的失效形态（KB-ENG-60）。
 */
function isProductionSource(rel: string): boolean {
  if (/\.(test|spec)\.(m|c)?[jt]sx?$/.test(rel)) return false;
  return !rel.split(path.sep).includes("__tests__");
}

function* walk(dir: string, prefix = ""): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const rel = prefix ? `${prefix}/${entry}` : entry;
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (SKIP_DIRS.has(entry)) continue;
      yield* walk(full, rel);
    } else if (SCAN_EXT.has(path.extname(entry))) {
      yield rel;
    }
  }
}

/**
 * 把注释 / 注释+字符串字面量替换成**等长空白**，只留可执行骨架。
 *
 * 两个面各有用途，不可混用（第一版守卫就栽在这里）：
 * - `maskCommentsAndStrings` —— 「这段代码真的读了密钥变量吗」。字符串里的同名
 *   文字是惰性的，不算用法；注释里提到更是必然（修复代码必须解释自己改了什么），
 *   不掩掉就会**假红**，而假红的失败信息会引导后人删掉正确的说明文字（KB-ENG-66）。
 * - `maskComments` —— 结构断言（如「代理是否注入了 `"x-api-token"` 请求头」）。
 *   此时**必须保留字符串字面量**，因为被断言的标识本身就是字符串；用上一个面
 *   断言会把自己的目标掩掉（本次实测：报 `set("x-api-token"` 不存在）。
 *
 * 两者都保留长度 ⇒ 行列号与原文一致，失败信息可以直接指到行。
 */
export function maskCommentsAndStrings(src: string): string {
  return mask(src, false);
}

export function maskComments(src: string): string {
  return mask(src, true);
}

function mask(src: string, keepStrings: boolean): string {
  const out = src.split("");
  const n = src.length;
  const blank = (j: number) => {
    out[j] = src[j] === "\n" ? "\n" : " ";
  };
  let i = 0;
  while (i < n) {
    const c = src[i];
    const next = src[i + 1];
    if (c === "/" && next === "/") {
      while (i < n && src[i] !== "\n") blank(i++);
    } else if (c === "/" && next === "*") {
      blank(i++);
      blank(i++);
      while (i < n && !(src[i] === "*" && src[i + 1] === "/")) blank(i++);
      if (i < n) {
        blank(i++);
        blank(i++);
      }
    } else if (c === '"' || c === "'" || c === "`") {
      // 字符串必须被「识别并整体跳过」，哪怕不掩码：否则 `"http://x"` 里的 `//`
      // 会被当成行注释，把该行后面的真实代码一并掩掉（漏报）。
      const quote = c;
      const start = i;
      i++;
      while (i < n && src[i] !== quote) {
        if (src[i] === "\\") {
          i += 2;
          continue;
        }
        i++;
      }
      if (i < n) i++;
      if (!keepStrings) for (let k = start; k < i; k++) blank(k);
    } else {
      i++;
    }
  }
  return out.join("");
}

/** 失败信息用的行号定位。 */
function lineOf(src: string, index: number): number {
  let line = 1;
  for (let i = 0; i < index && i < src.length; i++) if (src[i] === "\n") line++;
  return line;
}

type ScanResult = {
  files: string[];
  codeHits: string[];
  templateHits: string[];
  /** 生产源码里 NEXT_PUBLIC_ 变量的**总**读取次数（含非密钥），用于证明正则在真的匹配。 */
  envVarUsages: number;
};

function scanProductionSources(): ScanResult {
  const files: string[] = [];
  const codeHits: string[] = [];
  const templateHits: string[] = [];
  let envVarUsages = 0;

  for (const rel of walk(WEB_ROOT)) {
    if (!isProductionSource(rel)) continue;
    files.push(rel);
    const raw = readFileSync(path.join(WEB_ROOT, rel), "utf8");

    const skeleton = maskCommentsAndStrings(raw);
    NEXT_PUBLIC_ENV.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = NEXT_PUBLIC_ENV.exec(skeleton)) !== null) {
      envVarUsages++;
      if (SECRET_NAME.test(m[1])) codeHits.push(`${rel}:${lineOf(skeleton, m.index)} → ${m[1]}`);
    }

    NEXT_PUBLIC_ENV_IN_TEMPLATE.lastIndex = 0;
    while ((m = NEXT_PUBLIC_ENV_IN_TEMPLATE.exec(raw)) !== null) {
      if (SECRET_NAME.test(m[1])) templateHits.push(`${rel}:${lineOf(raw, m.index)} → ${m[1]}`);
    }
  }

  return { files: files.sort(), codeHits: codeHits.sort(), templateHits: templateHits.sort(), envVarUsages };
}

/** `.env*` 文件层面：把密钥声明成 NEXT_PUBLIC_ 前缀同样是泄露（构建期内联的源头）。 */
function scanEnvFiles(): { files: string[]; hits: string[] } {
  const files: string[] = [];
  const hits: string[] = [];
  for (const entry of readdirSync(WEB_ROOT)) {
    if (!entry.startsWith(".env")) continue;
    const full = path.join(WEB_ROOT, entry);
    if (!statSync(full).isFile()) continue;
    files.push(entry);
    const lines = readFileSync(full, "utf8").split("\n");
    lines.forEach((line, idx) => {
      const m = /^\s*(?:export\s+)?(NEXT_PUBLIC_[A-Za-z0-9_]*)/.exec(line);
      if (m && SECRET_NAME.test(m[1])) hits.push(`${entry}:${idx + 1} → ${m[1]}`);
    });
  }
  return { files: files.sort(), hits: hits.sort() };
}

describe("前端凭据禁令（NEXT_PUBLIC_* 不得承载密钥）", () => {
  it("生产源码的代码骨架里没有把凭据读进 NEXT_PUBLIC_ 变量", () => {
    const { codeHits } = scanProductionSources();
    expect(
      codeHits,
      `以下位置把凭据读进了会内联到客户端 bundle 的 NEXT_PUBLIC_ 变量：\n${codeHits.join("\n")}`,
    ).toEqual([]);
  });

  it("模板字面量插值里也没有漏网的凭据读取", () => {
    const { templateHits } = scanProductionSources();
    expect(templateHits, `模板字面量插值绕过了骨架扫描：\n${templateHits.join("\n")}`).toEqual([]);
  });

  it("扫描面非空且正则真的在工作（防止「无物可查 ⇒ 恒真」）", () => {
    const { files, envVarUsages } = scanProductionSources();
    // 遍历写窄 / 正则写错时，上面两条会**无声地恒真**。这里用活体证人把它们钉住：
    // 生产源码必须有足够多的可扫文件，且 NEXT_PUBLIC_ 读取确实被匹配到
    // （当前真实存在 NEXT_PUBLIC_API_BASE / NEXT_PUBLIC_WS_BASE 两处）。
    expect(files.length).toBeGreaterThanOrEqual(50);
    expect(envVarUsages).toBeGreaterThanOrEqual(2);
  });

  it(".env* 文件里没有把密钥声明成 NEXT_PUBLIC_ 前缀", () => {
    const { files, hits } = scanEnvFiles();
    expect(hits, `环境变量文件把凭据暴露给了客户端：\n${hits.join("\n")}`).toEqual([]);
    // 自证：确实读到了 .env 文件（否则遍历为空 ⇒ 恒真）
    expect(files.length).toBeGreaterThanOrEqual(1);
  });

  it("掩码器能区分「注释里提到」与「代码里真的用了」", () => {
    // 正例：真实取值必须被识别（否则守卫只是摆设）
    expect(hasSecretEnvUsage("const t = process.env.NEXT_PUBLIC_API_TOKEN;")).toBe(true);
    // 反例：仅写在注释 / 字符串里不得被判违规 —— 修复代码的注释必然要提到那个变量名。
    // ⚠️ 以下三条反例都**与正例同形**，逐条对应一种"提到"的写法。
    expect(hasSecretEnvUsage("// 历史实现用 process.env.NEXT_PUBLIC_API_TOKEN，已迁移")).toBe(false);
    expect(hasSecretEnvUsage("/** 勿用 process.env.NEXT_PUBLIC_API_KEY */ const a = 1;")).toBe(false);
    expect(hasSecretEnvUsage('const note = "process.env.NEXT_PUBLIC_SECRET";')).toBe(false);
    // 反向自证：掩码器**没有**把代码骨架一起掩掉（否则三条反例都"通过"而原因错）
    expect(maskCommentsAndStrings("const t = process.env.NEXT_PUBLIC_API_TOKEN;")).toContain(
      "process.env.NEXT_PUBLIC_API_TOKEN",
    );
    // 且 `maskComments` 保留字符串 —— 第一个失败版本正是用掩字符串的输出去断言字符串标识
    expect(maskComments('headers.set("x-api-token", token)')).toContain('"x-api-token"');
    expect(maskCommentsAndStrings('headers.set("x-api-token", token)')).not.toContain('"x-api-token"');
  });

  it("服务端代理确实注入写鉴权头（否则生产环境写接口会 401）", () => {
    const proxy = readFileSync(path.join(WEB_ROOT, "app/backend/[...path]/route.ts"), "utf8");
    const masked = maskComments(proxy);
    expect(masked).toContain("process.env.ASHARE_API_TOKEN");
    // 客户端伪造成一律丢弃，只认服务端环境变量
    expect(masked).toContain('headers.delete("x-api-token")');
    // 必须**只对非 GET/HEAD** 注入：给 GET 也加头等于把凭据撒到所有读请求上。
    // 用「注入点前一段代码里必须有方法判断」做相对位置断言，而不是只看全文有没有这句话。
    const setIdx = masked.indexOf('headers.set("x-api-token"');
    expect(setIdx, "代理里没有注入 x-api-token 的语句").toBeGreaterThan(-1);
    const window = masked.slice(Math.max(0, setIdx - 300), setIdx);
    expect(window, "注入 x-api-token 之前没有做请求方法判断").toContain('req.method !== "GET"');
  });
});

/** 测试用最小提取器：对给定源码片段跑同一套掩码 + 正则。 */
function hasSecretEnvUsage(snippet: string): boolean {
  const masked = maskCommentsAndStrings(snippet);
  const re = new RegExp(NEXT_PUBLIC_ENV.source, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(masked)) !== null) if (SECRET_NAME.test(m[1])) return true;
  return false;
}
