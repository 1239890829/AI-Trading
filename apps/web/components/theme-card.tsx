"use client";

import Link from "next/link";
import { fmt, fmtAmount, pctColor, pctText } from "@/lib/format";
import type { ThemeCard as ThemeCardType } from "@/types/market";

/**
 * 题材卡片 —— 看板的主容器。
 *
 * 设计约束（本项目 Operate 模式：可扫读性 > 表达，品牌在细节）：
 * - 不用渐变文字 / 玻璃拟态 / emoji 图标
 * - 边框与阴影不叠加；数字一律 tabular-nums 对齐
 * - 结论（health_note）必须落在最容易被看到的位置，而不是埋在最下面
 */

const STAGE_STYLE: Record<string, string> = {
  启动: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  发酵: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  高潮: "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  分歧: "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300",
  退潮: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
};

const FORMATION_STYLE: Record<string, string> = {
  成建制: "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  初步成形: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  零散: "border-zinc-300 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  个股行情: "border-zinc-200 bg-zinc-50 text-zinc-400 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-500",
};

/** 角色色阶：越靠前（定高度/定强度）越暖越亮，跟风/首板退到中性。 */
const ROLE_STYLE: Record<string, string> = {
  空间板: "border-rose-500/50 bg-rose-500/15 text-rose-700 dark:text-rose-300",
  龙头: "border-orange-500/50 bg-orange-500/15 text-orange-700 dark:text-orange-300",
  中军: "border-amber-500/50 bg-amber-500/15 text-amber-700 dark:text-amber-300",
  反包: "border-violet-500/50 bg-violet-500/15 text-violet-700 dark:text-violet-300",
  补涨: "border-sky-500/50 bg-sky-500/15 text-sky-700 dark:text-sky-300",
  跟风: "border-zinc-300 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  首板: "border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400",
  断板: "border-zinc-300 bg-zinc-100 text-zinc-400 line-through dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-500",
};

/** 封板时间分档：越早封板资金越坚决，色阶由暖到冷。 */
const SEAL_COLORS: Record<string, string> = {
  早盘: "bg-rose-500",
  上午: "bg-orange-400",
  午后: "bg-sky-400",
  尾盘: "bg-zinc-400",
};

function Badge({ className, children, title }: { className?: string; children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4 ${className ?? ""}`}
    >
      {children}
    </span>
  );
}

/** 小指标：上标签下数值，Operate 模式下靠这个做横向扫读。 */
function Stat({
  label,
  value,
  hint,
  valueClass,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  valueClass?: string;
}) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="text-[11px] text-zinc-400">{label}</div>
      <div className={`font-mono text-sm text-zinc-800 dark:text-zinc-100 ${valueClass ?? ""}`}>{value}</div>
    </div>
  );
}

/** 封板时间分布条：4 段堆叠，一眼看出是早盘抢筹还是尾盘偷袭。 */
function SealDistBar({ dist }: { dist: Record<string, number> }) {
  const order = ["早盘", "上午", "午后", "尾盘"];
  const total = order.reduce((s, k) => s + (dist[k] ?? 0), 0);
  if (total === 0) return <div className="h-2 w-full rounded-sm bg-zinc-200 dark:bg-zinc-800" />;
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-sm bg-zinc-200 dark:bg-zinc-800">
      {order.map((k) => {
        const v = dist[k] ?? 0;
        if (!v) return null;
        return (
          <div
            key={k}
            className={SEAL_COLORS[k]}
            style={{ width: `${(v / total) * 100}%` }}
            title={`${k} ${v} 只`}
          />
        );
      })}
    </div>
  );
}

/** 近 N 日涨停家数走势：判断资金是否持续进场，比单日家数更有信息量。 */
function TrendBars({ counts }: { counts: [string, number][] }) {
  if (!counts.length) return null;
  // 后端由近及远返回，展示需按时间从左到右（旧→新）
  const series = [...counts].reverse();
  const max = Math.max(...series.map(([, c]) => c), 1);
  return (
    <div className="flex items-end gap-[3px]" title={series.map(([d, c]) => `${d.slice(5)}: ${c}`).join("　")}>
      {series.map(([d, c]) => (
        <div key={d} className="flex flex-col items-center gap-0.5">
          <div
            className="w-2.5 rounded-sm bg-rose-500/70"
            style={{ height: `${Math.max(3, (c / max) * 22)}px` }}
          />
          <span className="font-mono text-[9px] leading-none text-zinc-400">{c}</span>
        </div>
      ))}
    </div>
  );
}

export function ThemeCardView({ card, rank }: { card: ThemeCardType; rank: number }) {
  const p = card.performance;
  const board = card.board;

  const ladderByBoard = new Map<number, typeof card.ladder>();
  for (const r of card.ladder) {
    const k = r.boards ?? 0;
    if (!ladderByBoard.has(k)) ladderByBoard.set(k, []);
    ladderByBoard.get(k)!.push(r);
  }
  const boardLevels = [...ladderByBoard.keys()].sort((a, b) => b - a);

  return (
    <section className="overflow-hidden rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
      {/* ── 头部：身份 + 阶段 + 强度 ───────────────────────────── */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <span className="font-mono text-xs text-zinc-400">#{rank}</span>
        <h3 className="text-base font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">{card.theme}</h3>
        <Badge className={FORMATION_STYLE[card.formation]} title="按涨停家数判定题材是否成建制">
          {card.formation}
        </Badge>
        <Badge className={STAGE_STYLE[card.stage]} title={card.stage_basis.join("；") || undefined}>
          {card.stage}
        </Badge>
        {board?.name && (
          <span className="text-xs text-zinc-400">
            板块 <span className="text-zinc-500 dark:text-zinc-300">{board.name}</span>
            {board.change_pct !== null && board.change_pct !== undefined && (
              <span className={`ml-1 font-mono ${pctColor(board.change_pct)}`}>{pctText(board.change_pct)}</span>
            )}
          </span>
        )}
        <div className="flex-1" />
        <div className="text-right">
          <div className="text-[11px] text-zinc-400">综合强度</div>
          <div className="font-mono text-lg leading-6 text-zinc-900 dark:text-zinc-50">{card.strength_score}</div>
        </div>
      </header>

      {/* ── 结论：健康度一句话（放在最上，避免被折叠忽略） ────────── */}
      <div className="border-b border-zinc-200 bg-zinc-50 px-4 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/40">
        <p className="text-[13px] leading-5 text-zinc-700 dark:text-zinc-200">{card.health_note}</p>
        {card.risks.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {card.risks.map((r) => (
              <span
                key={r}
                className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-700 dark:text-amber-300"
              >
                {r}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ── 板块整体表现（仅当题材名能匹配到东财板块） ─────────────── */}
      {board && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-zinc-200 px-4 py-2.5 sm:grid-cols-3 lg:grid-cols-6 dark:border-zinc-800">
          <Stat label="板块涨幅" value={<span className={pctColor(board.change_pct)}>{pctText(board.change_pct)}</span>} />
          <Stat label="涨跌家数" value={`${board.up_count ?? "--"} / ${board.down_count ?? "--"}`} hint="板块内上涨 / 下跌家数" />
          <Stat label="成交额" value={fmtAmount(board.amount)} />
          <Stat
            label="主力净流入"
            value={<span className={pctColor(board.main_net_inflow)}>{fmtAmount(board.main_net_inflow)}</span>}
            hint="东财口径主力资金净流入"
          />
          <Stat label="近 3 日" value={<span className={pctColor(board.chg_3d)}>{pctText(board.chg_3d)}</span>} hint="字段序推断，未经 K 线交叉验证" />
          <Stat label="近 5 日" value={<span className={pctColor(board.chg_5d)}>{pctText(board.chg_5d)}</span>} hint="字段序推断，未经 K 线交叉验证" />
        </div>
      )}

      {/* ── 连板天梯 ────────────────────────────────────────────── */}
      <div className="border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <div className="mb-2 flex items-baseline gap-2">
          <h4 className="text-xs font-medium text-zinc-500 dark:text-zinc-400">连板天梯</h4>
          <span className="text-[11px] text-zinc-400">按连板高度从高到低</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-left text-[11px] text-zinc-400 dark:border-zinc-800">
                <th className="w-10 py-1.5 font-medium">板</th>
                <th className="w-16 py-1.5 font-medium">角色</th>
                <th className="py-1.5 font-medium">名称</th>
                <th className="py-1.5 text-right font-medium">封单额</th>
                <th className="py-1.5 text-right font-medium" title="盘中打开涨停的次数">开板</th>
                <th className="py-1.5 text-right font-medium">换手</th>
                <th className="py-1.5 text-right font-medium">流通市值</th>
                <th className="py-1.5 text-right font-medium" title="首次封板时间，越早资金越坚决">封板</th>
              </tr>
            </thead>
            <tbody>
              {boardLevels.map((lv) =>
                ladderByBoard.get(lv)!.map((r, i) => (
                  <tr
                    key={r.symbol}
                    className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900/50"
                  >
                    <td className="py-1.5 align-middle">
                      {i === 0 ? (
                        <span className="inline-flex h-6 min-w-[26px] items-center justify-center rounded bg-zinc-900 px-1 font-mono text-xs font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900">
                          {lv}板
                        </span>
                      ) : (
                        <span className="pl-1 font-mono text-[11px] text-zinc-300 dark:text-zinc-600">同板</span>
                      )}
                    </td>
                    <td className="py-1.5 align-middle">
                      <Badge className={ROLE_STYLE[r.role]}>{r.role}</Badge>
                    </td>
                    <td className="py-1.5 align-middle">
                      <Link href={`/stock/${r.symbol}`} className="hover:text-rose-600 dark:hover:text-rose-400">
                        <span className="text-zinc-800 dark:text-zinc-100">{r.name ?? r.symbol}</span>
                        <span className="ml-1.5 font-mono text-[11px] text-zinc-400">{r.symbol}</span>
                      </Link>
                      {!r.is_primary && (
                        <span className="ml-1.5 text-[11px] text-zinc-400" title={`该股主属性为其他题材，此处为从属属性`}>
                          · 从属
                        </span>
                      )}
                      {r.other_themes.length > 0 && (
                        <div className="mt-0.5 truncate text-[11px] text-zinc-400" title={r.other_themes.join("、")}>
                          {r.other_themes.slice(0, 3).join("、")}
                          {r.other_themes.length > 3 && ` 等 ${r.other_themes.length} 个`}
                        </div>
                      )}
                    </td>
                    <td className="py-1.5 text-right align-middle font-mono text-zinc-700 dark:text-zinc-200">
                      {fmtAmount(r.seal_amount)}
                    </td>
                    <td className="py-1.5 text-right align-middle font-mono">
                      <span className={r.break_count ? "text-amber-600 dark:text-amber-400" : "text-zinc-500"}>
                        {r.break_count ?? "--"}
                      </span>
                    </td>
                    <td className="py-1.5 text-right align-middle font-mono text-zinc-700 dark:text-zinc-200">
                      {r.turnover_rate !== null && r.turnover_rate !== undefined ? `${fmt(r.turnover_rate)}%` : "--"}
                    </td>
                    <td className="py-1.5 text-right align-middle font-mono text-zinc-700 dark:text-zinc-200">
                      {fmtAmount(r.float_market_cap)}
                    </td>
                    <td className="py-1.5 text-right align-middle font-mono text-xs text-zinc-500">
                      {r.first_seal_time ?? "--"}
                      {r.seal_phase && <span className="ml-1 text-zinc-400">{r.seal_phase}</span>}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── 强度指标 ────────────────────────────────────────────── */}
      <div className="border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="涨停家数" value={p.limit_up_count} />
          <Stat
            label="梯队完整度"
            value={`${(p.echelon_completeness * 100).toFixed(0)}%`}
            hint="2 板到最高板之间各档是否都有票承接，100% 表示无断层"
            valueClass={p.echelon_completeness >= 0.8 ? "text-rose-600 dark:text-rose-400" : ""}
          />
          <Stat label="最高连板" value={`${p.max_boards} 板`} />
          <Stat
            label="开板率"
            value={`${(p.reopen_rate * 100).toFixed(0)}%`}
            hint="盘中打开过涨停的占比；全市场炸板率供对照"
            valueClass={p.reopen_rate >= 0.3 ? "text-amber-600 dark:text-amber-400" : ""}
          />
          <Stat
            label="封板成功率"
            value={`${(p.seal_success_rate * 100).toFixed(0)}%`}
            hint={`1 − 开板率；全市场炸板率 ${p.market_break_rate !== null && p.market_break_rate !== undefined ? (p.market_break_rate * 100).toFixed(1) : "--"}%`}
          />
          <Stat
            label="接力溢价"
            value={
              p.premium_median === null || p.premium_median === undefined ? (
                <span className="text-zinc-400">无样本</span>
              ) : (
                <span className={pctColor(p.premium_median)}>{pctText(p.premium_median)}</span>
              )
            }
            hint={`昨日该题材涨停股今日涨跌幅中位数（${p.premium_samples} 只样本）；为负说明接力亏钱`}
          />
        </div>

        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400">
              <span>封板时间分布（早盘封板占比高 = 资金坚决）</span>
              <span className="font-mono">封板质量 {p.seal_quality.toFixed(2)}</span>
            </div>
            <SealDistBar dist={p.seal_time_distribution} />
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400">
              <span>近 {p.daily_limit_up_counts.length} 日涨停家数</span>
              <span className="font-mono">连续活跃 {p.active_days} 天</span>
            </div>
            <TrendBars counts={p.daily_limit_up_counts} />
          </div>
        </div>
      </div>

      {/* ── 催化 / 阶段依据 / 横向比较 ───────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 px-4 py-3 lg:grid-cols-2">
        <div className="min-w-0">
          <h4 className="mb-1 text-xs font-medium text-zinc-500 dark:text-zinc-400">阶段判断依据</h4>
          {card.stage_basis.length ? (
            <ul className="space-y-0.5 text-[13px] text-zinc-600 dark:text-zinc-300">
              {card.stage_basis.map((b) => (
                <li key={b} className="flex gap-1.5">
                  <span className="text-zinc-300 dark:text-zinc-600">·</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-zinc-400">无额外依据（按默认规则判定）</p>
          )}
          <div className="mt-2 flex flex-wrap gap-1">
            {(card.raw_tags ?? []).slice(0, 8).map((t) => (
              <span key={t} className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                {t}
              </span>
            ))}
            {(card.raw_tags ?? []).length > 8 && (
              <span className="text-[11px] text-zinc-400">等 {card.raw_tags.length} 个标签</span>
            )}
          </div>
        </div>

        <div className="min-w-0">
          <h4 className="mb-1 text-xs font-medium text-zinc-500 dark:text-zinc-400">龙头 / 中军 / 补涨候选</h4>
          <div className="space-y-1 text-[13px]">
            {card.leaders.main ? (
              <div className="flex items-center gap-2">
                <Badge className={ROLE_STYLE[card.leaders.main.role]}>龙头</Badge>
                <Link href={`/stock/${card.leaders.main.symbol}`} className="text-zinc-800 hover:text-rose-600 dark:text-zinc-100 dark:hover:text-rose-400">
                  {card.leaders.main.name ?? card.leaders.main.symbol}
                </Link>
                <span className="font-mono text-xs text-zinc-400">{card.leaders.main.boards} 板</span>
              </div>
            ) : (
              <p className="text-zinc-400">未识别出龙头（题材内无 2 板以上）</p>
            )}
            {card.leaders.middle_weights.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <Badge className={ROLE_STYLE["中军"]}>中军</Badge>
                {card.leaders.middle_weights.map((m) => (
                  <Link key={m.symbol} href={`/stock/${m.symbol}`} className="text-zinc-700 hover:text-rose-600 dark:text-zinc-300 dark:hover:text-rose-400">
                    {m.name ?? m.symbol}
                    <span className="ml-1 font-mono text-xs text-zinc-400">{m.boards}板</span>
                  </Link>
                ))}
              </div>
            )}
            {card.leaders.candidates.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <Badge className={ROLE_STYLE["补涨"]}>可关注</Badge>
                {card.leaders.candidates.map((c) => (
                  <Link
                    key={c.symbol}
                    href={`/stock/${c.symbol}`}
                    title={c.reason}
                    className="text-zinc-700 hover:text-rose-600 dark:text-zinc-300 dark:hover:text-rose-400"
                  >
                    {c.name ?? c.symbol}
                    <span className="ml-1 font-mono text-xs text-zinc-400">{c.boards}板</span>
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
