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
    action.add_argument("--apply", metavar="PLAN_ID", help="比较原文件与已准备版本后仅替换授权及pin；需重载服务")
    action.add_argument("--prepare-grant", metavar="SOURCE_TASK_ID", help="仅初始四分析师阶段(root step 0)的known步骤取消可准备同版恢复；不启动任务")
    parser.add_argument("--run", help="prepare-grant的准确原run_id")
    parser.add_argument("--reviewed-source", action="append", default=[],
                        help="prepare时逐项声明已审源码名或具名packet_builder.owner_sources路径，必须等于实际差异")
    args = parser.parse_args(argv)
    if bool(args.run) != bool(args.prepare_grant):
        parser.error("--run与--prepare-grant必须一起使用")
    try:
        if args.prepare_grant:
            if args.reviewed_source:
                parser.error("同版恢复不声明源码变化")
            result = owner.prepare_grant(args.prepare_grant, args.run)
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
        from app.services.tradingagents_research import ResearchBlocked
        code = (str(error) if isinstance(error, owner.AuthorizationBlocked)
                else error.code if isinstance(error, ResearchBlocked) else None)
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
