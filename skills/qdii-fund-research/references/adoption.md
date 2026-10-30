# 完整流程的来源与适配

源：yennanliu/InvestSkill，`cba40eebdfcca95f0ccbde7afadd96014dfe685d`，[etf-analysis](https://github.com/yennanliu/InvestSkill/blob/cba40eebdfcca95f0ccbde7afadd96014dfe685d/prompts/etf-analysis.md)。本目录保留[MIT许可](../LICENSE)。本文件及主入口为2026-10-30国内QDII适配版，非上游原文。

完整保留身份→结构→成本/跟踪→流动性→底层持仓→组合重合→分配/风险→同类比较→失效/复核的尽调逻辑。原美国应税账户默认、美元AUM/价差阈值、固定总分与缺项重新分配权重、不具备材料时训练记忆补数、转买前五大股票建议均不采用。国内联接穿透、份额币种、申赎限制、双市场日历及净值公布时点成为同一步骤的正式条件，而非另造平行方法。

研究方法不提供行情授权或效果证明；代码许可不等于第三方数据展示许可。当前数据合同见`docs/product/cross-asset-workbench.md`，源能力与缺口归`docs/data/data-sources.md`。宿主是否发现该Skill须另取加载证据，文件存在不算加载通过。
