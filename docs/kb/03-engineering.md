# 工程教训（KB-ENG）

> 编码/测试/工具层面的坑。整合自 MEMORY.md「工具教训」与 2026-08-29 ~ 09-09 逐日日志。
> 发现遗漏按 00-INDEX 维护规则补录；与 KB-TRADE 的边界：影响代码正确性的归这里，影响盘面判断的归 02。

### KB-ENG-01 同文件多 Edit 并行必丢
- **现象**：同一文件并行多个 Edit 调用 → last-write-wins，先前的改动静默丢失（08-30 一天踩三次）。
- **正确做法**：同文件改动串行执行，每步后 grep 验证；批量修改用单次 python 脚本或依次 Edit。

### KB-ENG-02 zsh heredoc 陷阱
- **现象**：heredoc 内 `p = "..."` 触发 `=cmd` 文件名展开（command not found: p）；`${…}` 被 shell 提前替换。
- **正确做法**：写文件内容一律用 Edit/Write 工具，不用 heredoc 贴 Python；必须用时引号包裹 delimiter 并避免 `= ` 开头行。

### KB-ENG-03 后台 Bash 两个坑
- **cwd 不继承**：后台命令不继承工作目录——每条命令自带 `cd /abs/path`。
- **内联 `&` 被回收**：`xxx &` 启动的常驻服务随父 shell 退出被杀——常驻服务必须走专用 `run_in_background` 调用。

### KB-ENG-04 shell grep 假阴性
- **现象**：Bash 里 grep 搜不到确定存在的内容。
- **正确做法**：换 Grep 工具（ripgrep 封装，权限与 glob 处理不同）。

### KB-ENG-05 测试三坑
- 禁用 /tmp 固定路径（会被污染/清理）→ 用 `tmp_path` fixture。
- 「源码切片 exec」式测试在函数被抽取到别处后**静默失效**（还在测旧拷贝）。
- 契约变更（如新增过滤条件）必须同步改测试 fixture，否则测试测的是旧契约（09-09 气泡 name 过滤事故）。

### KB-ENG-06 agent-browser 交互坑
- `dispatchEvent(new PointerEvent(...))` 合成事件**不触发 React 状态**——交互态验证必须用真实指针（`agent-browser hover`）。
- 受控 input 需原生 setter；`is enabled` 在元素未挂载时误报 true（禁用态用 eval 读 `b.disabled`）；network route stub 后先 eval fetch 探针确认钉住。

### KB-ENG-07 建表两件套
- 新表必须写 Alembic 迁移（不能只靠 create_all——生产库不会自动建表）。
- `Base` 在 `app.models.watchlist`（`app.core.db` **没有** Base）；新模型要在 `main.py` 的 `_REGISTERED_MODELS` 注册。

### KB-ENG-08 SQLite/SQLAlchemy
- datetime 过滤**永远用 datetime 对象**（存的是空格分隔格式，与 "…T00:00:00" 字符串比较恒 False——09-04 预算防线曾静默失效）；统一 `evolution._utc_cutoff_today()`。
- `get_session_factory()` 返回 sessionmaker，须再调一次才是 session。

### KB-ENG-09 接口契约变更必须跑端点冒烟
- **现象**：09-09 `_serialize_event` 返回 pydantic Out 模型（不可 item 赋值）→ /api/alerts/events 500；本机测试库没暴露（因为没跑端点）。
- **正确做法**：改返回结构/契约的代码，合入前对该端点 curl 冒烟一次；pydantic→dict 用 `.model_dump()`。

### KB-ENG-10 JSX 批量修改
- 只换开标签漏闭标签 → tsc 立刻抓到（先替换后必跑 tsc）。
- import 插入用 `rfind('import ')` 会插进 `import type {}` 多行块中间——用 Edit 手工插。

### KB-ENG-11 curl 中文查询串
- 直接拼中文=Invalid HTTP request；用 `curl --get --data-urlencode "theme=糖"`。

### KB-ENG-12 前端 dev server 会夜间死亡
- Next dev（3000）整夜挂着可能死掉——「页面空白/接口 404」先查服务存活再查代码。
- **正确做法**：每日晨检 `lsof -nP -iTCP:3000 -sTCP:LISTEN`。

### KB-ENG-13 过滤后数组是唯一渲染源
- **现象**：09-09 通知中心——空态判断用过滤后 bySession，列表 map 却遍历原始数组 → tab 计数 0 但 DOM 残留 48 行。
- **正确做法**：空态判断与列表渲染必须消费同一个过滤后数组。

### KB-ENG-14 /tmp 启动脚本会被系统清理
- `/tmp/start_backend_8000.sh` 被临时目录清理导致「启动失败」假象。
- **正确做法**：脚本内容固定（cd backend && exec uvicorn），被清就重建；更稳的做法是记进 deployment.md 用时重写。

### KB-ENG-15 memory 日志写入用绝对路径
- 后台/复合命令里相对路径 `>> .workbuddy/memory/...` 因 cwd 漂移写错位置（09-08 实际发生）。
- **正确做法**：一律绝对路径 `/Users/hezifeng/Desktop/project/ms/ashare-ai-trader/.workbuddy/memory/YYYY-MM-DD.md`。


### KB-ENG-16 flex 高度链：中间层块级元素会让 overflow-y-auto 全部失效
- **现象**（两次踩坑，用户明令不再犯）：面板内容超高时页面无法滚动——overflow-auto 容器拿到的
  是「内容高」而非「受限高」，滚动永不触发。
- **根因**：flex 高度链在中间层断掉。FadeSwap 等 tab wrapper 若是**块级元素**（未设
  flex flex-col），其子面板的 flex-1/min-h-0 不产生约束高度 → 内部 overflow-y-auto 失效。
- **正确做法**：高度链每一层都要接力——wrapper `flex min-h-0 flex-1 flex flex-col overflow-hidden`，
  面板根 `h-full min-h-0` + 自管滚动（overflow-y-auto 或内部分栏各自滚）。
  **新建任何「左树右详情/上表下详情」面板时，先把高度链从头到尾写对再写内容。**
- **自查命令**：浏览器 eval `pane.scrollHeight > pane.clientHeight && pane.scrollTop 可设置`。
