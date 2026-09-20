---
name: github-star-batch-eval
description: 批量评测用户 GitHub star 分组（Lists）新增仓库的完整工作流：拉取分组→按 star 时间窗识别新增批次→克隆到本地实际运行验证（严禁仅凭 README 判断）→测可靠性/延迟/与现有栈互补性→剔除不可用项→对可用项做最小化整合落地（依赖声明/门禁/真数据验收）→输出优先级整合报告。当用户说"star 分组新增了项目""帮我评测我收藏的 X 类仓库""把 star 里这批项目筛选一下"时使用。分组拉取细节复用 github-star-lists skill。
agent_created: true
---

# GitHub Star 分组批量仓库评测与整合

## 总原则

- **运行实证 > 文档判断**：每个仓库必须装起来跑通核心功能才算评估过；README 自述、star 数、文档质量只作旁证。
- **剔除要有证据**：不可用结论必须带实测证据（安装失败堆栈 / 运行输出 / 成功率数据），并给观察名单条目（附重启评估的触发条件）。
- **整合最小化**：只做与现有栈互补的最小集成；扩展面不进核心热链路；每一步过门禁。

## 工作流

### 1. 拉取分组与识别新增批次

1. 前置：`source ~/.zshenv` 取 `GITHUB_TOKEN`；GitHub API 走本机活代理（先探测可用代理端口，如 `curl -x http://127.0.0.1:7897`；会话环境变量里的代理可能是死的）。
2. 分组清单与仓库详情用 **github-star-lists skill** 的 GraphQL 查询（UserListItems 是 union，必须 `... on Repository {}`）。
3. 新增识别：再查 `viewer.starredRepositories(first: 100, orderBy: {field: STARRED_AT, direction: DESC}) { edges { starredAt node { nameWithOwner } } }`，与分组清单求交集；**star 时间在数分钟~同一小时窗口内的簇 = 一批**。列出批次时间窗，确认范围无歧义后再动手。
4. 先查 `docs/kb/05-repo-tracker.md`、Git 历史与现有研究证据，确认同批或更早仓库是否已评估过，避免重复劳动；已有结论引用现役记录。

### 2. 克隆与评测环境

- 统一放 `/tmp/star-eval-<YYYYMMDD>/`，`git clone --depth 1` 并行克隆（走活代理：`git -c http.proxy=http://127.0.0.1:7897 clone ...`）。
- **每仓库独立 venv**（`python -m venv venv-<name>`），绝不装进业务 venv 或全局。
- 先做结构扫描（顶层文件 / 依赖清单 / 入口），列假设清单再逐个验证。

### 3. 实测（每个仓库必做）

- 装依赖 → import → 跑核心功能。测试脚本要点：
  - 真实数据输入；每个调用独立超时守护（ThreadPoolExecutor + future.result(timeout)），一个挂起不拖垮整批；
  - 脚本顶部清 `HTTP_PROXY/HTTPS_PROXY` 并设 `NO_PROXY='*'`（macOS 死系统代理是隐形杀手，requests 会读系统代理而 curl --noproxy 逻辑不适用 python）；
  - **可靠性用重复测量**：同一接口连打 N 次统计成功率与延迟分布（单次成功≠可用）；
  - 用 `multiprocessing` 的库在脚本里必须 `if __name__ == "__main__"` 守卫（macOS spawn 重入会自递归卡死）。
- 评估维度：① 真实功能与适用场景；② 与现有栈的互补性（能力差集，重叠面不计分）；③ 可靠性/延迟/维护活跃度（pushedAt）；④ 安装摩擦（依赖冲突、平台限制、Python 版本兼容）。
- 平台错配（如 Windows UI 自动化库）、跨大版本依赖锁死、作者自述重构中且文档与代码脱节 → 实测确认后剔除。

### 4. 环境级坑（详见 references/pitfalls.md）

老 sdist 包在新 pip 下解包 EEXIST、pip 走死代理、数据源域名分层可用性（同仓库不同后端域名成败迥异）、大依赖安装超时 → 处置方案全在 references/pitfalls.md，动手前先读。

### 5. 整合落地（仅对"可用且互补"项）

1. 先说清集成形态：数据源类 → 独立扩展服务（懒加载 + TTLCache + 显式降级三态，不进行情热链路）；工具库类 → scripts/或单点封装；平台类 → 记录部署前提，不强推。
2. 依赖落地三件套：requirements.txt（注释说明用途与边界）+ requirements.lock（精确版本，特殊包用 vendor wheel 并带 sha256）+ 本地 venv 实装验证。
3. 代码：懒加载 import（重库不能拖慢启动）、异常显式带 kind、NaN/前导零等数据清洗、缓存走项目统一 TTLCache（异常不缓存）。
4. 门禁：pyflakes → 全量 pytest（`--basetemp=/tmp/pytest-basetemp`，跑完 `git checkout -- data/trade_calendar.json`）→ 前端没动也要 tsc --noEmit 兜底 → 重启服务后**真数据 curl 验收**（验收以实际返回为准）。
5. 全量测试若深夜跑出"白天全绿"的失败，先查时间窗敏感（UTC/本地时区混用、日期翻转），确认预存 bug 顺手修掉并注明。

### 6. 产出报告

每仓库一节：功能实证 / 互补性 / 可靠性数据 / 结论（保留+整合方式 | 剔除+证据+观察名单触发条件 | 可拓展方向）。最后给优先级整合清单与执行结果。评测产物留在 /tmp（临时），结论沉淀进项目记忆。

## 报告口径示例

- `akshare：三池/宏观/两融/龙虎榜第二源稳定可用（150-350ms，push2ex+datacenter 域），个股 K 线面本机被墙不接 → 保留，整合为扩展数据服务`
- `easytrader：requirements 锁 2018 年版本（pandas 0.23）py3.13 装不上，核心通道 pywinauto 为 Windows 客户端自动化，平台错配 → 剔除；触发条件：用户具备 Windows/miniQMT 环境需要实盘桥时重评`
