# Governance Audit Checklist

## 目标与价值
- [ ] 最终目标明确
- [ ] 真实消费者明确
- [ ] 没有把已有实现误当目标
- [ ] 存在不做/保持现状选项

## 证据
- [ ] 事实 / 推断 / 未知分开
- [ ] 有失败样本、负例、漏报、弃权
- [ ] 没有未来信息泄漏
- [ ] 相对 baseline 可比较
- [ ] 没把低层证据升级为效果结论

## 机制生命周期
- [ ] version / evidence_asof
- [ ] applicability / non-applicability
- [ ] counterexample / falsifier
- [ ] review_due / revisit_trigger
- [ ] degradation signals
- [ ] Champion / Challenger 或 baseline
- [ ] rollback / exit / revival

## 成本与复杂度
- [ ] token / 调用
- [ ] CI / 算力
- [ ] 延迟
- [ ] 人工注意力
- [ ] 维护复杂度
- [ ] 数据/供应商成本
- [ ] 权限/隐私面

## AI / Agent
- [ ] 确定性任务优先零模型
- [ ] 模型只处理真正需要的语义/推理
- [ ] confidence 不当事实
- [ ] 实验不能自行晋级生产
- [ ] 高风险动作有人类边界

## 知识库
- [ ] 来源/时间/版本可追溯
- [ ] 有实际消费者
- [ ] 有使用记录
- [ ] 能区分独立增量与重复背景
- [ ] 过期与反例可回写

## 执行
- [ ] 当前阶段门正确
- [ ] 没有跨过更低阻断项
- [ ] 一轮一个主切片
- [ ] 验收与效果前置分开
- [ ] 完成后状态和证据回填唯一来源

## 收尾
- [ ] 重大决定完成传播核对
- [ ] 没有第二套状态表
- [ ] 任务能关闭/退出/等待重开
- [ ] 历史证据保留但不绑架未来
