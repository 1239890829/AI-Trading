# 完整财报核验流程的适配

源：Jakeschincariol/trader-skill，`5189e79b9a480755a3733ac9713f5353981dbe18`，[trader-analyst](https://github.com/Jakeschincariol/trader-skill/blob/5189e79b9a480755a3733ac9713f5353981dbe18/trader-analyst/SKILL.md)。保留[MIT许可](../LICENSE)。2026-10-30修改为项目中性只读流程。

保留原文件→XBRL季度/同比/TTM→EX-99.1及10-Q上下文→推导/滞后/一次性项目核查→短结论和保存。未采用broker、税务、13F跟单、国会跟单或筛选/metrics自动消费；不复制上游示例数值。上游facts.py未安装为应用数据源；后续若确需程序化读取，只能评估一个解析owner，不能与InvestSkill再装一套同事实源。联系人和SEC访问条件未核就不执行，不向用户声称该源已经上线。
