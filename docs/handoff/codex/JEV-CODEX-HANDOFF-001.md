# JEV-CODEX-HANDOFF-001

## 父任务
JEV-ECOSYSTEM-002

## 目标
实现JEV基础能力，使其成为决策解释层。

## 允许
- 新增JEV模块
- 实现数据适配
- 实现解释存储
- 实现降级机制

## 禁止
- 修改picks核心评分
- 修改交易逻辑
- 让JEV直接产生交易信号

## 执行顺序
1. Schema
2. Snapshot Adapter
3. Explanation Storage
4. Fallback
5. API
6. 前端接入

## 验收
- 可追踪版本
- JEV不可用时系统仍运行
- 测试通过
