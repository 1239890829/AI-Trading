"""Maintain only the current installation's existing full-research authorization."""
from __future__ import annotations

import argparse
import json
import re


def main(argv=None) -> int:
    from app.services import _tradingagents_authorization as owner

    parser = argparse.ArgumentParser(description="原范围手工研究版本承接；默认只读，不创建模型或延期授权")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true", help="将准确已审源码差异冻结为0600私有安装计划")
    action.add_argument("--prepare-retained-unknown", metavar="USAGE_ID", help="按新明确用户许可保留具名终态未知用量；仅选股后研究，不清账、不启动")
    action.add_argument("--apply", metavar="PLAN_ID", help="比较原文件与已准备版本后仅替换授权及pin；需重载服务")
    action.add_argument("--prepare-grant", metavar="SOURCE_TASK_ID", help="仅初始四分析师阶段(root step 0)的known步骤取消可准备同版恢复；不启动任务")
    parser.add_argument("--run", help="prepare-grant的准确原run_id")
    parser.add_argument("--cooperative-prelaunch-tail", action="store_true", help="显式核验单一未启动末尾编号；不补用量或重跑原答")
    parser.add_argument("--source-rebind-plan", metavar="PLAN_ID", help="仅原恢复代码版本变化时引用正式已安装重绑计划")
    parser.add_argument("--reviewed-source", action="append", default=[],
                        help="prepare时逐项声明已审源码名或具名packet_builder.owner_sources路径，必须等于实际差异")
    parser.add_argument("--source-budget-date", help="保留许可的原北京日YYYY-MM-DD")
    parser.add_argument("--source-scope", help="保留许可的准确原消费scope")
    parser.add_argument("--source-purpose", help="保留许可的准确原purpose")
    parser.add_argument("--source-state", help="保留许可的准确原终态")
    parser.add_argument("--authorization-reason", help="新明确用户授权的范围与保全说明；不得含凭据或请求context")
    args = parser.parse_args(argv)
    if bool(args.run) != bool(args.prepare_grant):
        parser.error("--run与--prepare-grant必须一起使用")
    if (args.cooperative_prelaunch_tail or args.source_rebind_plan) and not args.prepare_grant:
        parser.error("末尾编号与原计划选项只用于prepare-grant")
    if args.source_rebind_plan and not args.cooperative_prelaunch_tail:
        parser.error("原计划选项须明确选择末尾编号核验")
    retained_args = [args.source_budget_date, args.source_scope, args.source_purpose, args.source_state, args.authorization_reason]
    if bool(args.prepare_retained_unknown) != all(value is not None for value in retained_args) or (any(value is not None for value in retained_args) and not args.prepare_retained_unknown):
        parser.error("新保留许可必须同时明确原usage ID/date/scope/purpose/state与用户授权理由")
    try:
        if args.prepare_retained_unknown:
            fact = {"usage_id": args.prepare_retained_unknown, "budget_date": args.source_budget_date,
                    "scope": args.source_scope, "purpose": args.source_purpose, "state": args.source_state}
            result = owner.prepare_retained_unknown(fact, args.authorization_reason, args.reviewed_source)
        elif args.prepare_grant:
            if args.reviewed_source:
                parser.error("同版恢复不声明源码变化")
            result = owner.prepare_grant(args.prepare_grant, args.run,
                **({"cooperative_prelaunch_tail": True, "source_rebind_plan": args.source_rebind_plan}
                   if args.cooperative_prelaunch_tail else {}))
        elif args.apply:
            if args.reviewed_source:
                parser.error("apply只消费已冻结计划")
            result = owner.apply_plan(args.apply)
        elif args.prepare:
            result = owner.prepare_rebind(args.reviewed_source)
        else:
            if args.reviewed_source:
                parser.error("源码复核声明只用于prepare")
            result = owner.inspect_rebind()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        from app.services.agent_budget import BudgetError
        from app.services.tradingagents_research import ResearchBlocked
        code = (str(error) if isinstance(error, owner.AuthorizationBlocked)
                else error.code if isinstance(error, (ResearchBlocked, BudgetError)) else None)
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code):
            code = "authorization_maintenance_unavailable"
        result = {"state": "blocked", "code": code}
        if code == "recovery_cooperative_checkpoint_invalid":
            from app.services.tradingagents_recovery import COOPERATIVE_STAGE_MESSAGE
            result["message"] = COOPERATIVE_STAGE_MESSAGE
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
