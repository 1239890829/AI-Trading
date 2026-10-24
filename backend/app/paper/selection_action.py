"""Selection-bound paper context. Reads never initialize funds or generate a decision."""
from datetime import datetime, timedelta, timezone
import json
import re
import time

from sqlalchemy import and_, case, func, or_, select

from app.core.bjtime import beijing_now, to_beijing
from app.models.daily_pick import DailyPickSet
from app.models.notification_outbox import BuyPointConsumption
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot
from app.models.paper import PaperActionReceipt, PaperOrder
from app.paper.engine import calc_fee, is_valid_price, limit_block_reason
from app.paper.execution_guard import window_issue
from app.picks.buy_point import evaluate_buy_points
from app.picks.opportunity_learning import latest_notification_execution
from app.picks.review_contract import generated_time, object_json, selection_version


def _source(sf, ref, version, now):
    match = re.fullmatch(r"(daily|intraday):(\d{4}-\d{2}-\d{2}):(\d{6})", ref or "")
    if not match:
        return None, None, None, ("selection_missing", "原入选身份无效")
    kind, day, symbol = match.groups()
    with sf() as db:
        daily = db.scalar(select(DailyPickSet).where(DailyPickSet.date == day))
        meta = object_json(daily.meta) if daily else {}
        items = json.loads(daily.items or "[]") if daily else []
        item = next((i for i in items if i.get("symbol") == symbol), None)
        if kind == "daily":
            if not item:
                return symbol, None, None, ("selection_missing", "原每日入选记录不存在")
            if selection_version(day, meta, items) != version:
                return symbol, None, None, ("selection_superseded", "入选版本已变化，请核对当前原记录")
            if generated_time(meta) is None or generated_time(meta) > now:
                return symbol, None, None, ("selection_missing", "原入选时点尚未发生或无法核实")
        else:
            row = db.scalar(select(Snapshot).where(Snapshot.trade_date == day, Snapshot.symbol == symbol,
                Snapshot.run_id == version, Snapshot.scenario == "intraday_opportunity",
                Snapshot.stage == "rank", Snapshot.decision == "ranked").order_by(Snapshot.id).limit(1))
            evidence = object_json(row.evidence) if row else {}
            limit = evidence.get("effective_limit")
            created = to_beijing(row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at) if row else None
            if (row is None or row.data_state != "ready" or not is_valid_price(row.entry_price)
                    or created.date().isoformat() != day or created > now
                    or evidence.get("selection_entry_contract") != "selection-entry-v1"
                    or evidence.get("within_display_capacity") != 1
                    or not isinstance(limit, (int, float)) or isinstance(limit, bool)
                    or row.rank is None or not 1 <= row.rank <= limit):
                return symbol, None, None, ("selection_missing", "原盘中入选及展示资格没有有效归档")
        if day != now.date().isoformat():
            return symbol, item, meta, ("historical_selection", "这是历史入选记录，不能作为今天的新模拟买点")
        if not item:
            return symbol, None, meta, ("no_actionable_decision", "盘中候选尚无当前每日买点执行资格")
    return symbol, item, meta, None


def selection_binding(sf, ref, version, now, *, decision_id=None, decision_version=None):
    symbol, item, meta, issue = _source(sf, ref, version, now)
    if issue:
        return symbol, item, meta, None, issue
    decision = latest_notification_execution(now.date().isoformat(), sf).get(symbol) or {}
    from app.picks.execution_qualification import resolve_execution_item
    source_item = item
    item, context, qualification_issue = resolve_execution_item(item, decision, version, now)
    if qualification_issue:
        code = "observation_only" if source_item.get("observation_only") and "execution_seed" not in source_item else "no_actionable_decision"
        return symbol, item, meta, decision, (code, qualification_issue)
    if context is not None:
        meta = {**meta, "gate": context["gate"], "market_phase": context["phase"]}
    if (decision.get("archived_decision") != "eligible" or decision.get("gate_decision") != "passed"
            or (decision.get("executable_snapshot") or {}).get("state") != "ready"
            or (decision.get("reference_entry") or {}).get("as_of") != meta.get("generated_at")):
        return symbol, item, meta, decision, ("no_actionable_decision", "当前没有同版已确认可执行买点；未把研究结论当交易资格")
    if ((decision_id is not None and decision_id != decision.get("decision_id"))
            or (decision_version is not None and decision_version != decision.get("decision_version"))):
        return symbol, item, meta, decision, ("decision_superseded", "执行判定版本已变化，请重新预检")
    return symbol, item, meta, decision, None


def execution_status(sf, ref, version, *, request_id=None, decision=None, exclude_event_key=None):
    """Only existing main action receipts/consumers and actual order state."""
    match = re.fullmatch(r"(daily|intraday):(\d{4}-\d{2}-\d{2}):(\d{6})", ref or "")
    day, symbol = (match.group(2), match.group(3)) if match else (None, None)
    with sf() as db:
        def projection(result=None, state="not_started", reason="尚无关联模拟动作"):
            result = object_json(result)
            order = None
            if result:
                reason = result.get("reason") or reason
                if result.get("id") is not None or result.get("order_id") is not None:
                    row = db.scalar(select(PaperOrder).where(PaperOrder.scope == "main",
                        PaperOrder.id == result.get("id", result.get("order_id"))))
                    if row is not None and row.symbol == symbol and row.side == "buy":
                        state = row.status if row.status in {"pending", "filled", "rejected", "cancelled"} else "unknown"
                        order = {"id": row.id, "status": row.status, "filled_price": row.filled_price,
                                 "fee": row.fee, "reason": row.reason}
                        reason = row.reason or {"filled": "模拟成交已保存", "cancelled": "原模拟委托已撤销"}.get(row.status, "模拟订单状态已保存")
                    else:
                        state, reason = "unknown", "原动作回执保留，但当前订单不存在；未重复下单"
                elif result.get("execution_unknown") or result.get("status") in {"pending", "filled", "unknown"}:
                    state = "unknown"
                elif result.get("status") == "rejected" or result.get("opened") is False:
                    state = "rejected"
            return {"state": state, "reason": reason, "order": order}

        candidates = [projection()]
        draft = case((func.json_valid(PaperActionReceipt.draft), PaperActionReceipt.draft), else_="{}")
        manual_result = case((func.json_valid(PaperActionReceipt.result), PaperActionReceipt.result), else_="{}")
        query = select(PaperActionReceipt).where(PaperActionReceipt.scope == "main",
            func.json_extract(draft, "$.selection_ref") == ref,
            func.json_extract(draft, "$.source_version") == version)
        if request_id:
            query = query.where(PaperActionReceipt.request_id == str(request_id))
        query = query.outerjoin(PaperOrder, (PaperOrder.id == func.json_extract(manual_result, "$.id"))
                                & (PaperOrder.scope == "main"))
        priority = case((PaperOrder.status.in_(("filled", "pending")), 0),
                        (func.json_extract(manual_result, "$.status").in_(("filled", "pending")), 1), else_=2)
        for receipt in db.scalars(query.order_by(priority, PaperActionReceipt.created_at.desc())):
            result = object_json(receipt.result)
            candidates.append(projection(result, "not_started" if result else "unknown",
                                         "原动作结果缺失，须核对旧回执"))
        if match:
            from app.picks.buy_point import _buy_point_dedup_key
            payload = case((func.json_valid(BuyPointConsumption.payload), BuyPointConsumption.payload), else_="{}")
            selected_ref = func.json_extract(payload, "$.alert.meta.selection_ref")
            selected_version = func.json_extract(payload, "$.alert.meta.selection_source_version")
            exact_binding = and_(selected_ref == ref, selected_version == version)
            legacy_identity = or_(
                and_(func.json_extract(payload, "$.alert.symbol") == symbol,
                     func.json_extract(payload, "$.alert.meta.trade_date") == day),
                BuyPointConsumption.event_key == _buy_point_dedup_key(day, symbol),
                func.json_extract(payload, "$.alert.meta.execution_ref.decision_id") == decision.get("decision_id")
                if decision and decision.get("decision_id") else False)
            # Current decision/version controls only new qualification. It cannot
            # erase another already running or unresolved action for this selection.
            query = select(BuyPointConsumption).where(BuyPointConsumption.consumer == "position",
                or_(exact_binding, and_(or_(selected_ref.is_(None), selected_version.is_(None)), legacy_identity)))
            if exclude_event_key is not None:
                query = query.where(BuyPointConsumption.event_key != exclude_event_key)
            for progress in db.scalars(query.order_by(BuyPointConsumption.updated_at_ms.desc())):
                saved = object_json(progress.payload)
                meta = object_json(object_json(saved.get("alert")).get("meta"))
                result = object_json(saved.get("execution_result"))
                exact = meta.get("selection_ref") == ref and meta.get("selection_source_version") == version
                if not exact:
                    # Legacy same-stock/day work is an unresolved occupant. Do
                    # not fabricate a fill or a source-version association for it.
                    if (progress.state in {"pending", "running", "unknown"}
                            or result.get("execution_unknown") or result.get("opened") is True
                            or result.get("accepted") is True or result.get("order_id") is not None):
                        candidates.append(projection(state="unknown", reason=progress.reason or "旧自动动作缺少入选版本绑定，须先核对"))
                    continue
                state = {"running": "pending", "pending": "pending", "expired": "expired", "unknown": "unknown",
                         "done": "unknown", "skipped": "not_started"}.get(progress.state, "unknown")
                if progress.state == "unknown" and result.get("order_id") is None and result.get("id") is None:
                    result = {}  # A partial opened=False cannot clear an unknown owner.
                candidates.append(projection(result, state, progress.reason))
        # A later rejected manual attempt cannot hide a running/unknown durable
        # consumer. Live orders have the same precedence over old receipts.
        precedence = {"filled": 0, "pending": 1, "unknown": 2, "cancelled": 3,
                      "rejected": 4, "expired": 5, "not_started": 6}
        return min(candidates, key=lambda value: precedence[value["state"]])


def automatic_selection_binding(engine, symbol, context, *, matching_order_id=None, quote=None):
    """Resolve a claimed automatic intent without letting its own running slot self-lock."""
    fields = ("selection_ref", "source_version", "decision_id", "decision_version", "consumer_event_key")
    if (engine.scope != "main" or not isinstance(context, dict)
            or any(not isinstance(context.get(key), str) or not context[key] for key in fields)
            or context["selection_ref"] != f"daily:{beijing_now().date().isoformat()}:{symbol}"):
        return None, ("selection_binding_missing", "自动买点缺少同一原入选、执行判定与消费身份，未执行")
    expiry = context.get("expires_at_ms")
    if type(expiry) is not int or (matching_order_id is None and expiry <= int(time.time() * 1000)):
        return None, ("original_action_expired", "原自动动作已过期，未重复执行")
    with engine._sf() as db:
        owner = db.get(BuyPointConsumption, (context["consumer_event_key"], "position"))
        saved = object_json(object_json(object_json(owner.payload).get("alert")).get("meta")) if owner else {}
        reference = object_json(saved.get("execution_ref"))
        result = object_json(object_json(owner.payload).get("execution_result")) if owner else {}
        owner_valid = owner is not None and (owner.state == "running" if matching_order_id is None else
            owner.state == "done" and result.get("order_id") == matching_order_id and not result.get("execution_unknown"))
        if (not owner_valid or owner.expires_at_ms != expiry
                or saved.get("selection_ref") != context["selection_ref"]
                or saved.get("selection_source_version") != context["source_version"]
                or reference.get("decision_id") != context["decision_id"]
                or reference.get("decision_version") != context["decision_version"]):
            return None, ("automatic_owner_unknown", "原自动消费归属已变化，需核对原动作")
    selected, item, meta, decision, issue = selection_binding(engine._sf, context["selection_ref"],
        context["source_version"], beijing_now(), decision_id=context["decision_id"],
        decision_version=context["decision_version"])
    if issue:
        return None, issue
    if selected != symbol:
        return None, ("selection_binding_missing", "自动动作证券与原入选不一致")
    if quote is not None:
        hits, skips = evaluate_buy_points([item], gate_stand=bool((meta.get("gate") or {}).get("stand_aside")),
            quotes={symbol: {"price": quote.price, "change_pct": getattr(quote, "change_pct", None),
                             "prev_close": getattr(quote, "prev_close", None)}})
        if not hits:
            return None, ("no_actionable_decision", (skips[0].get("reason") if skips else None) or "当前买点条件未通过")
    existing = execution_status(engine._sf, context["selection_ref"], context["source_version"],
        decision=decision, exclude_event_key=context["consumer_event_key"])
    if existing["state"] in {"pending", "filled", "unknown"}:
        return None, ("execution_exists", "原入选已有其他模拟动作或待核结果，未重复执行")
    return meta, None


def bind_automatic_order(db, order, context):
    """Commit the original consumer's accepted order in the financial transaction."""
    db.flush()
    owner = db.get(BuyPointConsumption, (context["consumer_event_key"], "position"))
    payload = object_json(owner.payload) if owner else {}
    alert = object_json(payload.get("alert"))
    meta = object_json(alert.get("meta"))
    reference = object_json(meta.get("execution_ref"))
    previous = object_json(payload.get("execution_result"))
    if (order.scope != "main" or order.side != "buy" or order.status not in {"pending", "filled"}
            or owner is None or owner.state != "running"
            or owner.expires_at_ms != context["expires_at_ms"]
            or owner.expires_at_ms <= int(time.time() * 1000)
            or meta.get("selection_ref") != context["selection_ref"]
            or meta.get("selection_source_version") != context["source_version"]
            or reference.get("decision_id") != context["decision_id"]
            or reference.get("decision_version") != context["decision_version"]
            or previous.get("order_id") is not None):
        raise ValueError("automatic order owner changed before financial commit")
    payload["execution_result"] = {"order_id": order.id, "status": order.status,
        "opened": order.status == "filled", "accepted": True, "pending": order.status == "pending",
        "execution_unknown": False, "decision_id": context["decision_id"],
        "decision_version": context["decision_version"]}
    owner.payload = json.dumps(payload, ensure_ascii=False)
    # Preserve running and its claim timestamp. Only the consumer settles work.
    db.add(owner)


def _manual_pending_issue(db, engine, order, quote):
    safe = case((func.json_valid(PaperActionReceipt.result), PaperActionReceipt.result), else_="{}")
    receipts = list(db.scalars(select(PaperActionReceipt).where(PaperActionReceipt.scope == "main",
        func.json_extract(safe, "$.id") == order.id,
        PaperActionReceipt.created_at >= order.created_at).limit(2)))
    if len(receipts) != 1:
        return "原买入挂单归属无法核实，暂不撮合"
    receipt = receipts[0]
    draft, result = object_json(receipt.draft), object_json(receipt.result)
    if (type(result.get("id")) is not int or result["id"] != order.id
            or result.get("symbol") != order.symbol or result.get("side") != order.side
            or result.get("status") != "pending" or draft.get("symbol") != order.symbol
            or draft.get("side") != order.side or draft.get("price") != order.price
            or draft.get("quantity") != order.quantity
            or receipt.created_at < order.created_at):
        return "原买入挂单回执与订单不一致，暂不撮合"
    fields = ("selection_ref", "source_version", "decision_id", "decision_version")
    if not any(draft.get(field) for field in fields):
        return None
    if not all(isinstance(draft.get(field), str) and draft[field] for field in fields):
        return "原买入挂单入选身份不完整，暂不撮合"
    symbol, item, meta, _decision, issue = selection_binding(engine._sf, draft["selection_ref"],
        draft["source_version"], beijing_now(), decision_id=draft["decision_id"],
        decision_version=draft["decision_version"])
    if issue:
        return issue[1]
    if symbol != order.symbol:
        return "原买入挂单与入选证券不一致，暂不撮合"
    hits, skips = evaluate_buy_points([item], gate_stand=bool((meta.get("gate") or {}).get("stand_aside")),
        quotes={symbol: {"price": quote.price, "change_pct": getattr(quote, "change_pct", None),
                         "prev_close": getattr(quote, "prev_close", None)}})
    return None if hits else (skips[0].get("reason") if skips else None) or "当前买点条件未通过"


def automatic_pending_issue(engine, order_id, symbol, quote):
    """Recheck main order ownership without creating another intent."""
    if engine.scope != "main":
        return None
    with engine._sf() as db:
        order = db.get(PaperOrder, order_id)
        if order is None or order.scope != "main" or order.symbol != symbol:
            return "原挂单身份无法核实，暂不撮合"
        payload = case((func.json_valid(BuyPointConsumption.payload), BuyPointConsumption.payload), else_="{}")
        owners = list(db.scalars(select(BuyPointConsumption).where(
            BuyPointConsumption.consumer == "position",
            func.json_extract(payload, "$.execution_result.order_id") == order_id).limit(2)))
        if not owners:
            return _manual_pending_issue(db, engine, order, quote) if order.side == "buy" else None
        if len(owners) != 1:
            return "原自动订单的消费归属不唯一，暂不撮合"
        if order.side != "buy":
            return "原自动订单方向与买点不一致，暂不撮合"
        owner = owners[0]
        meta = object_json(object_json(object_json(owner.payload).get("alert")).get("meta"))
        reference = object_json(meta.get("execution_ref"))
        context = {"selection_ref": meta.get("selection_ref"),
            "source_version": meta.get("selection_source_version"),
            "decision_id": reference.get("decision_id"), "decision_version": reference.get("decision_version"),
            "consumer_event_key": owner.event_key, "expires_at_ms": owner.expires_at_ms}
    _meta, issue = automatic_selection_binding(engine, symbol, context, matching_order_id=order_id, quote=quote)
    return issue[1] if issue else None


async def action_issue(engine, item, meta, symbol, price, quantity, quote, *, days=None):
    issue = await engine.execution_context_issue(symbol, quote, days=days)
    if issue:
        return issue
    if not is_valid_price(price) or not is_valid_price(getattr(quote, "price", None)):
        return "quote_unavailable", "没有有效执行价格"
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0 or quantity % 100:
        return "invalid_lot", "模拟买入数量须为100股整数倍"
    hits, skips = evaluate_buy_points([item], gate_stand=bool((meta.get("gate") or {}).get("stand_aside")),
        quotes={symbol: {"price": quote.price, "change_pct": getattr(quote, "change_pct", None),
                         "prev_close": getattr(quote, "prev_close", None)}})
    if not hits:
        return "no_actionable_decision", (skips[0].get("reason") if skips else None) or "当前买点条件未通过"
    blocked = limit_block_reason(quote, "buy", price)
    if blocked:
        return "risk_rejected", blocked
    risk = engine._risk_engine
    if risk is None:
        return "risk_rejected", "模拟风控尚未装配，不能确认执行"
    try:
        account, positions = engine.risk_check_context(risk.hub, initialize=False)
    except Exception:
        return "risk_unavailable", "模拟账户或风控上下文暂不可核实，不能确认执行"
    if not account.get("account_created", True) or not isinstance(account.get("cash"), (int, float)):
        return "account_uninitialized", "模拟账户未初始化；只读预检不创建账户"
    try:
        checked = risk.check_order(symbol=symbol, side="buy", price=price, quantity=quantity,
            account=account, positions=positions, quote=quote.model_dump())
    except Exception:
        return "risk_unavailable", "模拟风控检查未完成，不能确认执行"
    if not checked.get("allowed"):
        return "risk_rejected", "；".join(checked.get("reasons") or ["未通过当前模拟风控"])
    if price * quantity + calc_fee("buy", price, quantity) > account["cash"]:
        return "risk_rejected", "可用模拟资金不足以覆盖订单与费用"
    return None


async def read_selection_action(engine, ref, version, *, price=None, quantity=100, request_id=None):
    now = beijing_now()
    if engine.scope != "main":
        raise ValueError("此入口仅服务main模拟账户")
    symbol, item, meta, decision, issue = selection_binding(engine._sf, ref, version, now)
    execution = execution_status(engine._sf, ref, version, request_id=request_id, decision=decision)
    if request_id:
        active = execution_status(engine._sf, ref, version, decision=decision)
        if active["state"] in {"pending", "filled", "unknown"}:
            execution = active
    if not issue and execution["state"] in {"pending", "filled", "unknown"}:
        issue = ("execution_exists", "此入选已有模拟动作或待核结果，请先核对原委托")
    quote, preflight = None, None
    closed = window_issue(now)
    issue = issue or (closed if closed and closed[0] == "market_closed" else None)
    if not issue:
        try:
            quote = await engine._quote_fn(symbol)
        except Exception:
            issue = ("quote_unavailable", "当前报价读取失败，暂不能确认执行")
        price = price if price is not None else getattr(quote, "price", None)
        issue = issue or await action_issue(engine, item, meta, symbol, price, quantity, quote)
        checked = datetime.now(timezone.utc)
        preflight = {"allowed": issue is None, "side": "buy", "price": price, "quantity": quantity,
            "fee": calc_fee("buy", price, quantity) if is_valid_price(price) else None,
            "checked_at": checked.isoformat(), "expires_at": (checked + timedelta(seconds=30)).isoformat(),
            "decision_id": decision.get("decision_id"), "decision_version": decision.get("decision_version")}
    return {"scope": "main", "read_only": True,
        "selection": {"ref": ref, "symbol": symbol, "source_version": version},
        "eligibility": {"state": "unknown" if issue and issue[0] in {"calendar_unknown", "quote_unavailable", "quote_untrusted", "risk_unavailable"}
                        else "blocked" if issue else "ready", "reason_code": issue[0] if issue else "ready",
                        "reasons": [issue[1]] if issue else []},
        "quote": {"price": getattr(quote, "price", None), "source": getattr(quote, "source", None),
                  "as_of": to_beijing(quote.data_timestamp).isoformat()
                  if isinstance(getattr(quote, "data_timestamp", None), datetime) and quote.data_timestamp.tzinfo else None,
                  "quality": getattr(quote, "quality", None)} if quote else None,
        "preflight": preflight, "execution": execution}


async def validate_bound_action(engine, body, quote, *, days=None):
    """Recheck the declared source and current decision before any paper write."""
    if not getattr(body, "selection_ref", None):
        return
    from fastapi import HTTPException
    symbol, item, meta, _decision, issue = selection_binding(engine._sf,
        body.selection_ref, body.source_version, beijing_now(),
        decision_id=body.decision_id, decision_version=body.decision_version)
    if engine.scope != "main" or symbol != body.symbol or body.side != "buy":
        issue = ("selection_missing", "模拟动作与原入选证券或账户不一致")
    if not issue:
        existing = execution_status(engine._sf, body.selection_ref, body.source_version, decision=_decision)
        if existing["state"] in {"pending", "filled", "unknown"}:
            raise HTTPException(409, detail={"code": "execution_exists",
                "reason": "此入选已有模拟动作或待核结果，不能用新请求编号重复下单"})
    if not issue:
        issue = await action_issue(engine, item, meta, symbol, body.price, body.quantity, quote, days=days)
    if issue:
        raise HTTPException(422, detail={"code": issue[0], "reason": issue[1]})
