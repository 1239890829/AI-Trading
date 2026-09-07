#!/usr/bin/env python3
"""已采纳参考仓库的周期性更新跟踪（只发现与汇总，不自动引入/合并任何变更）。

用法：
    .venv/bin/python scripts/repo_watch.py                       # 增量运行（无基线则建立基线）
    .venv/bin/python scripts/repo_watch.py --dry-run --since 2026-08-15
    .venv/bin/python scripts/repo_watch.py --repo vnpy/vnpy --dry-run

机制：
- GitHub REST：仓库元数据 + compare(base_sha...default_branch) 一次拿增量 commits 与 files
- 分类：breaking > deprecated > feat > perf > fix > other（匹配 commit message 前 6 行，含 PR 标题）
- 价值标注：files 触及该仓「已采纳模块路径」→ ⭐ 高价值；breaking/deprecated → ⚠️ 决策风险
- 产物：周报 docs/repo-watch/YYYY-MM-DD.md；基线 backend/data/repo-watch/state.json
- 纪律：单仓失败不中断整批（错误单独计数进报告）；脚本绝不 pull/install/改代码
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # backend/
STATE_PATH = ROOT / "data" / "repo-watch" / "state.json"
REPORT_DIR = ROOT.parent / "docs" / "repo-watch"
API = "https://api.github.com"
CST = timezone(timedelta(hours=8))
NOW = lambda: datetime.now(CST)  # noqa: E731

# ---------------------------------------------------------------------------
# 已采纳参考仓库清单（增删仓库只改这里；tier: adopted=成果已吸收/experimental=决策载体）
# watch_paths: 该仓哪些路径变更算「触及已采纳模块」→ ⭐ 高价值
# ---------------------------------------------------------------------------
@dataclass
class RepoWatch:
    owner: str
    name: str
    tier: str
    adopted: str  # 采纳了什么（报告标题行）
    watch_paths: list  # type: list[str]
    upstream: str = "github"  # github=可跟踪 | unreachable=上游公网不可达（供应链风险提示，不拉取）

    @property
    def key(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.key}"


WATCH_REPOS = [
    RepoWatch(
        "handsomejustin", "easy_tdx", "adopted",
        "运行时核心 TDX 数据依赖（requirements.lock 锁 easy-tdx==1.20.12；tdx_kline/heatmap/minute_backfill 在用）",
        ["src/easy_tdx/"],
        upstream="unreachable",  # 2026-09-07 实测：GitHub 双名 404、PyPI 公网 404、pip index 无——安装来源私有不透明
    ),
    RepoWatch(
        "microsoft", "qlib", "adopted",
        "Alpha158/360 因子定义源头（已吸收进 app/factors/ 候选登记册与注册表）",
        ["qlib/contrib/data/", "qlib/data/"],
    ),
    RepoWatch(
        "sngyai", "Sequoia-X", "adopted",
        "A 股事件策略 6 式（涨停洗盘/停机坪/高而窄旗等，登记于因子候选事件通道）",
        ["sequoia_x/strategy/"],
    ),
    RepoWatch(
        "virattt", "ai-hedge-fund", "adopted",
        "基本面/PEAD 因子族登记（缺财报数据，观察位）",
        ["src/", "hedge_fund/"],
    ),
    RepoWatch(
        "myhhub", "stock", "adopted",
        "TA-Lib 指标/CDL 形态参考（因子候选 C 层）",
        ["instock/"],
    ),
    RepoWatch(
        "vnpy", "vnpy", "experimental",
        "交易网关首选替代（macOS 冒烟实测通过，未接入；上游变更=接入方案重估信号）",
        ["vnpy/gateway/", "vnpy/trader/", "vnpy/app/", "vnpy/event/", "vnpy/cta/"],
    ),
    RepoWatch(
        "shidenggui", "easytrader", "experimental",
        "排查结论：三重结构性问题不采纳（2026-09-07）；其若复活=该决策需重估",
        ["easytrader/", "easyutils/"],
    ),
]

# ---------------------------------------------------------------------------
# commit message 语义分类（含 PR 标题——merge commit 标题在 message 前 6 行内）
# ---------------------------------------------------------------------------
PAT_BREAKING = re.compile(r"!:\s|breaking change|incompatible|bc break|major release", re.I)
PAT_DEPRECATE = re.compile(r"deprecat|弃用|移除|remove[d]?\s+(support|the\s+\S+\s+(api|feature|module|endpoint))", re.I)
PAT_FEAT = re.compile(r"\bfeat\b|^add\s|^adds?\s|support|introduce|新增|支持", re.I)
PAT_PERF = re.compile(r"\bperf\b|optimi|refactor|speed|faster|优化|重构", re.I)
PAT_FIX = re.compile(r"\bfix|bug|修复", re.I)


def classify(message: str) -> str:
    head = "\n".join(message.splitlines()[:6])
    if PAT_BREAKING.search(head):
        return "breaking"
    if PAT_DEPRECATE.search(head):
        return "deprecated"
    if PAT_FEAT.search(head):
        return "feat"
    if PAT_PERF.search(head):
        return "perf"
    if PAT_FIX.search(head):
        return "fix"
    return "other"


# ---------------------------------------------------------------------------
# GitHub REST（urllib；UA 必带；token 可选；仅网络类错误重试 1 次）
# ---------------------------------------------------------------------------
class Gh:
    def __init__(self, token: str | None):
        self.headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ashare-repo-watch",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, url: str, retries: int = 1):
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(200).decode("utf-8", "replace")
            except Exception:
                pass
            # 404 属「资源不存在」（force-push 后基线失效等），不重试
            if e.code == 404 or retries <= 0:
                raise RuntimeError(f"HTTP {e.code}: {body[:120]}") from e
            return self.get(url, retries - 1)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if retries <= 0:
                raise RuntimeError(f"network: {e}") from e
            return self.get(url, retries - 1)


# ---------------------------------------------------------------------------
# 单仓库跟踪
# ---------------------------------------------------------------------------
def _short_err(e: str) -> str:
    """压缩 GitHub 错误 JSON 为可读短语。"""
    m = re.search(r'"message"\s*:\s*"([^"]{1,60})', e)
    code = re.search(r"HTTP (\d{3})", e)
    return f"{code.group(1) + ' ' if code else ''}{m.group(1) if m else e[:60]}"


def watch_repo(repo: RepoWatch, prev: dict, gh: Gh, since_override: str | None) -> dict:
    if repo.upstream == "unreachable":
        return {
            "repo": repo.key, "tier": repo.tier, "adopted": repo.adopted, "url": repo.url,
            "upstream_unreachable": True,
        }

    meta = gh.get(f"{API}/repos/{repo.key}")
    branch = meta["default_branch"]
    head_branch_sha = branch_tip(gh, repo, branch)

    out = {
        "repo": repo.key,
        "tier": repo.tier,
        "adopted": repo.adopted,
        "url": repo.url,
        "default_branch": branch,
        "head_sha": head_branch_sha,
        "pushed_at": meta["pushed_at"],
        "archived": bool(meta["archived"]),
        "stars": meta["stargazers_count"],
        "base_sha": (prev or {}).get("head_sha"),
        "since": since_override,
        "commits": [],
        "counts": {},
        "hit_files": [],
        "total_commits": 0,
        "truncated": False,
        "degraded": None,
        "release": None,
        "new_release": False,
    }

    base = since_override or (prev or {}).get("head_sha")
    if not base:
        out["baseline_new"] = True
        return out

    # 时间窗模式（--since）：GitHub compare 只接受 sha/tag，日期窗直接走 commits?since=
    # （拿不到 files → 无文件级命中判定，报告显式标注降级）
    if since_override:
        iso = since_override if "T" in since_override else f"{since_override}T00:00:00Z"
        out["degraded"] = "--since 时间窗模式：仅列提交，无文件级命中判定（正式周期运行用 SHA 基线 compare）"
        try:
            lst = gh.get(f"{API}/repos/{repo.key}/commits?since={urllib.parse.quote(iso)}&per_page=100")
        except RuntimeError as e:
            out["error"] = str(e)
            return out
        for c in lst:
            msg = c["commit"]["message"]
            out["commits"].append({
                "sha": c["sha"][:9], "date": c["commit"]["author"]["date"][:10],
                "msg": msg.splitlines()[0][:110], "cat": classify(msg),
            })
        out["total_commits"] = len(lst)
    else:
        base_sha = prev.get("head_sha")
        try:
            cmp_url = f"{API}/repos/{repo.key}/compare/{urllib.parse.quote(base_sha, safe='')}...{urllib.parse.quote(branch, safe='')}"
            cmp_data = gh.get(cmp_url)
            out["total_commits"] = cmp_data.get("total_commits", len(cmp_data.get("commits", [])))
            out["truncated"] = out["total_commits"] > 250
            for c in cmp_data.get("commits", []):
                msg = c["commit"]["message"]
                out["commits"].append({
                    "sha": c["sha"][:9],
                    "date": c["commit"]["author"]["date"][:10],
                    "msg": msg.splitlines()[0][:110],
                    "cat": classify(msg),
                })
            for f in cmp_data.get("files", []):
                fn = f["filename"]
                if any(fn.startswith(p) for p in repo.watch_paths):
                    out["hit_files"].append({"file": fn, "status": f["status"], "delta": f"+{f['additions']}/-{f['deletions']}"})
        except RuntimeError as e:
            # compare 失败（force-push 致基线 sha 不在历史等）→ 降级按上次运行时间列提交（无 files）
            iso = (prev.get("pushed_at") or NOW().date().isoformat())
            out["degraded"] = f"compare 不可用（{_short_err(e)}）→ 降级按上次运行时间({iso[:10]})列提交，无文件级命中判定"
            try:
                lst = gh.get(f"{API}/repos/{repo.key}/commits?since={urllib.parse.quote(iso)}&per_page=100")
            except RuntimeError as e2:
                out["error"] = str(e2)
                return out
            for c in lst:
                msg = c["commit"]["message"]
                out["commits"].append({
                    "sha": c["sha"][:9], "date": c["commit"]["author"]["date"][:10],
                    "msg": msg.splitlines()[0][:110], "cat": classify(msg),
                })
            out["total_commits"] = len(lst)

    counts: dict[str, int] = {}
    for c in out["commits"]:
        counts[c["cat"]] = counts.get(c["cat"], 0) + 1
    out["counts"] = counts

    try:
        rel = gh.get(f"{API}/repos/{repo.key}/releases/latest")
        tag = rel.get("tag_name")
        out["release"] = tag
        out["new_release"] = bool(tag and (prev or {}).get("release_tag") not in (None, tag))
    except RuntimeError:
        out["release"] = None  # 无 release 是常态，不算失败
    return out


def branch_tip(gh: Gh, repo: RepoWatch, branch: str) -> str:
    """default_branch 可能含 '/'，经 branch 接口拿尖端 sha（compare 的 head 也用它）。"""
    data = gh.get(f"{API}/repos/{repo.key}/branches/{urllib.parse.quote(branch, safe='')}")
    return data["commit"]["sha"]


def worth_flags(r: dict, repo: RepoWatch) -> list:
    """「值得评估」判定（透明规则，报告里引用）。"""
    flags = []
    if r.get("upstream_unreachable"):
        flags.append("⚠️ 运行时依赖上游公网不可达（供应链风险）——每期固定提示，直至处置")
        return flags
    if r.get("archived"):
        flags.append("⚠️ 仓库已归档（Archived）——若在用需立即重估替代")
    breaking = [c for c in r["commits"] if c["cat"] == "breaking"]
    dep = [c for c in r["commits"] if c["cat"] == "deprecated"]
    if r["tier"] == "adopted" and (breaking or dep):
        flags.append("⚠️ 破坏性变更/弃用触及 adopted 仓库——检查已吸收成果是否受影响")
    if r["tier"] == "experimental" and r.get("total_commits", 0) >= 10:
        flags.append(f"⚠️ experimental 仓库窗口内 {r['total_commits']} 次提交——旧结论（接入/不采纳）可能过期，需重估")
    if r["hit_files"]:
        flags.append(f"⭐ {len(r['hit_files'])} 个变更文件触及已采纳模块路径——值得评估吸收")
    if r.get("new_release"):
        flags.append(f"👀 新发布 {r['release']}")
    return flags


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
CAT_CN = {"feat": "新功能", "perf": "优化", "fix": "修复", "deprecated": "弃用", "breaking": "破坏", "other": "其他"}


def render_report(results: list, window_note: str) -> str:
    now = NOW()
    L: list[str] = []
    L.append(f"# 仓库跟踪周报 · {now.date().isoformat()}")
    L.append("")
    L.append(f"> 自动生成：`backend/scripts/repo_watch.py`｜{window_note}")
    L.append("> ⚠️ 本任务只发现与汇总更新，**不自动引入/合并任何变更**；所有新内容需人工全方位评估后再决定采纳。")
    L.append("")
    # 概览
    L.append("## 概览")
    L.append("")
    L.append("| 仓库 | 层级 | 状态 | 增量提交 | " + " | ".join(CAT_CN[c] for c in ["feat", "perf", "fix", "deprecated", "breaking"]) + " | 新发布 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r, _repo in results:
        if "error" in r:
            L.append(f"| [{r['repo']}]({r['url']}) | {r['tier']} | ❌ 失败 | - | - | - | - | - | - |")
            continue
        if r.get("upstream_unreachable"):
            L.append(f"| [{r['repo']}]({r['url']}) | {r['tier']} | ⚠️ 上游不可达 | - | - | - | - | - | - |")
            continue
        c = r["counts"]
        status = "基线已建立" if r.get("baseline_new") else ("归档" if r["archived"] else "正常")
        L.append(
            f"| [{r['repo']}]({r['url']}) | {r['tier']} | {status} | {r['total_commits']} | "
            f"{c.get('feat', 0)} | {c.get('perf', 0)} | {c.get('fix', 0)} | {c.get('deprecated', 0)} | {c.get('breaking', 0)} | "
            f"{('[' + r['release'] + ']') if r.get('new_release') else '-'} |"
        )
    L.append("")

    # 待评估清单
    L.append("## 待评估清单（按优先级）")
    L.append("")
    any_eval = False
    for r, repo in results:
        flags = worth_flags(r, repo) if "error" not in r else []
        if not flags:
            continue
        any_eval = True
        L.append(f"### {r['repo']}")
        for f in flags:
            L.append(f"- {f}")
        if r.get("hit_files"):
            for h in r["hit_files"][:10]:
                L.append(f"  - `{h['file']}`（{h['status']}，{h['delta']}）")
            if len(r["hit_files"]) > 10:
                L.append(f"  - …共 {len(r['hit_files'])} 个命中文件")
        L.append("")
    if not any_eval:
        L.append("本窗口无需评估项（全部仓库无实质变更或仅普通修复）。")
        L.append("")

    # 各仓详情
    L.append("## 各仓详情")
    for r, repo in results:
        L.append("")
        L.append(f"### {r['repo']} — {repo.tier}")
        L.append(f"采纳内容：{repo.adopted}")
        if "error" in r:
            L.append(f"❌ 拉取失败：{r['error']}（下窗口自动重试）")
            continue
        if r.get("upstream_unreachable"):
            L.append("- ⚠️ **上游仓库公网不可达**（GitHub 仓库 404、PyPI 公网无此包、pip index 解析失败，2026-09-07 实测）——"
                     "该依赖来源私有/不透明，属供应链风险：无法自动跟踪上游变更，且无法确认上游是否仍在维护。")
            L.append("- 处置建议：确认当初的安装来源（私有镜像/直接 wheel）；若上游不可持续，评估替代（如 mootdx/pytdx 直连协议实现）。")
            L.append("- 当前锁定版本：`easy-tdx==1.20.12`（requirements.lock）。")
            continue
        if r.get("baseline_new"):
            L.append(f"- 首次跟踪，基线已建立：{r['default_branch']} @ `{r['head_sha'][:9]}`（pushed {r['pushed_at'][:10]}，{r['stars']}★）")
            L.append("- 下次运行起开始增量对比。")
            continue
        L.append(f"- {r['default_branch']}：`{r['base_sha'][:9] if r['base_sha'] else '?'} → {r['head_sha'][:9]}`（pushed {r['pushed_at'][:10]}，{r['stars']}★）"
                 + (f"｜最新 release：{r['release']}" if r.get("release") else ""))
        if r.get("degraded"):
            L.append(f"- ⚠️ 降级：{r['degraded']}")
        if r.get("truncated"):
            L.append(f"- ⚠️ 窗口内 {r['total_commits']} 次提交超过 compare 上限（250），仅展示最近部分——基线过旧，建议尽快运行")
        c = r["counts"]
        L.append(f"- 分类：{('，'.join(f'{CAT_CN[k]} {c[k]}' for k in CAT_CN if c.get(k))) or '无'}")
        for cat, title, limit in (("breaking", "破坏性变更", 20), ("deprecated", "弃用", 20), ("feat", "新功能", 15), ("perf", "优化", 10)):
            rows = [x for x in r["commits"] if x["cat"] == cat]
            if rows:
                L.append(f"- **{title}**：")
                for x in rows[:limit]:
                    L.append(f"  - `{x['sha']}` {x['date']} {x['msg']}")
                if len(rows) > limit:
                    L.append(f"  - …另有 {len(rows) - limit} 条")
        if not (c.get("feat") or c.get("perf") or c.get("breaking") or c.get("deprecated")):
            L.append("- 本窗口仅普通修复/杂项。")
    L.append("")

    # 无变更
    quiet = [r["repo"] for r, _ in results
             if "error" not in r and not r.get("baseline_new") and not r.get("upstream_unreachable") and r["total_commits"] == 0]
    if quiet:
        L.append("## 无变更仓库")
        L.append("")
        L.append("、".join(quiet))
        L.append("")
    # 失败
    errs = [(r["repo"], r["error"]) for r, _ in results if "error" in r]
    L.append("## 失败与降级")
    L.append("")
    if errs:
        for name, e in errs:
            L.append(f"- ❌ {name}：{e}")
        L.append("- 失败仓库的基线保持不变，下窗口自动重试补齐。")
    else:
        L.append("全部仓库拉取成功。")
    L.append("")
    L.append("---")
    L.append(f"*生成时间 {now.strftime('%Y-%m-%d %H:%M:%S')} CST｜跟踪 {len(WATCH_REPOS)} 个仓库｜纪律：只发现汇总，不引入变更*")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# state 与 CLI
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "last_run_at": None, "repos": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description="已采纳参考仓库更新跟踪（只汇总，不引入）")
    ap.add_argument("--dry-run", action="store_true", help="不写基线 state，报告输出到 stdout（或 --out）")
    ap.add_argument("--out", help="报告输出路径（默认：docs/repo-watch/YYYY-MM-DD.md；dry-run 默认 stdout）")
    ap.add_argument("--since", help="覆盖基线：从该日期(YYYY-MM-DD)开始对比（验证/补跑用）")
    ap.add_argument("--repo", help="只跟踪指定仓库 owner/name")
    args = ap.parse_args()

    state = load_state()
    gh = Gh(os.environ.get("GITHUB_TOKEN"))
    repos = WATCH_REPOS
    if args.repo:
        repos = [r for r in repos if r.key == args.repo]
        if not repos:
            print(f"未收录仓库 {args.repo}，已收录：{', '.join(r.key for r in WATCH_REPOS)}", file=sys.stderr)
            return 2

    results = []
    new_state_entries: dict[str, dict] = {}
    for repo in repos:
        prev = state["repos"].get(repo.key)
        try:
            r = watch_repo(repo, prev, gh, args.since)
        except RuntimeError as e:
            r = {"repo": repo.key, "tier": repo.tier, "adopted": repo.adopted, "url": repo.url, "error": str(e)}
        results.append((r, repo))
        if "error" not in r and "head_sha" in r:
            new_state_entries[repo.key] = {
                "head_sha": r["head_sha"], "pushed_at": r["pushed_at"],
                "default_branch": r["default_branch"], "release_tag": r.get("release"),
                "updated_at": NOW().isoformat(timespec="seconds"),
            }

    # 窗口说明
    trackable = [r for r, _ in results if not r.get("upstream_unreachable")]
    if args.since:
        window_note = f"窗口：{args.since} → {NOW().date().isoformat()}（--since 覆盖基线）"
    elif trackable and all((r.get("baseline_new") or "error" in r) for r in trackable):
        window_note = "首次运行：建立基线（不报变更），下次运行起增量对比"
    else:
        last = state.get("last_run_at")
        window_note = f"窗口：上次运行 {last or '?'} → 本次 {NOW().isoformat(timespec='seconds')}"

    report = render_report(results, window_note)

    if args.dry_run:
        if args.out:
            Path(args.out).write_text(report, encoding="utf-8")
            print(f"[dry-run] 报告 → {args.out}（state 未写入）")
        else:
            print(report)
        return 0

    # 更新 state：只覆盖本次跑成功的仓库
    state["last_run_at"] = NOW().isoformat(timespec="seconds")
    state["repos"].update(new_state_entries)
    save_state(state)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else REPORT_DIR / f"{NOW().date().isoformat()}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"报告 → {out_path}")
    print(f"基线 → {STATE_PATH}（{len(new_state_entries)}/{len(repos)} 仓库更新）")
    failed = [r["repo"] for r, _ in results if "error" in r]
    if failed:
        print(f"失败仓库（已跳过，基线未动）：{'，'.join(failed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
