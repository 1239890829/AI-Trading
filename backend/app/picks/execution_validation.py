"""Read existing execution owners together; never merge capital or infer real fills."""
from datetime import date, datetime, time, timedelta, timezone
import math

from sqlalchemy import select

from app.core.bjtime import BJ_TZ, to_beijing
from app.core.db import get_session_factory
from app.models.hunting_shadow import HuntingShadowAttempt as Attempt
from app.models.paper import PaperOrder
from app.picks.shadow import load_execution_log

SCOPES = ('shadow', 'hunting_shadow')


def _finite(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None


def _state(order):
    if order is None:
        return 'unknown'
    if order.status == 'filled':
        if (_finite(order.filled_price) is None or _finite(order.fee) is None
                or order.filled_price <= 0 or order.fee < 0 or not isinstance(order.quantity,int) or order.quantity <= 0):
            return 'unknown'
    return order.status if order.status in ('filled','rejected','pending','cancelled') else 'unknown'


def execution_report(start, end, *, session_factory=None, log_reader=None):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last-first).days <= 366:
        raise ValueError('执行对照范围须为1至367天')
    start, end = first.isoformat(), last.isoformat()
    lower = datetime.combine(first,time(),BJ_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    upper = datetime.combine(last+timedelta(days=1),time(),BJ_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    sf, read_log = session_factory or get_session_factory(), log_reader or load_execution_log
    with sf() as db:
        orders = list(db.scalars(select(PaperOrder).where(PaperOrder.scope.in_(SCOPES),
            PaperOrder.created_at >= lower, PaperOrder.created_at < upper).order_by(PaperOrder.created_at,PaperOrder.id).limit(50001)))
        attempts = list(db.scalars(select(Attempt).where(Attempt.trade_date >= start, Attempt.trade_date <= end)
            .order_by(Attempt.created_at,Attempt.id).limit(50001)))
        if len(orders)>50000 or len(attempts)>50000:
            raise ValueError('执行记录过多，请缩短范围；未截断统计分母')
        linked_ids = sorted({a.entry_order_id for a in attempts if a.entry_order_id is not None})
        linked = {}
        for offset in range(0,len(linked_ids),500):
            ids = linked_ids[offset:offset+500]
            linked.update({o.id:o for o in db.scalars(select(PaperOrder).where(PaperOrder.id.in_(ids)))})
        attempt_rows=[]
        for a in attempts:
            order=linked.get(a.entry_order_id)
            valid = order is not None and order.scope=='hunting_shadow' and order.symbol==a.symbol and order.side=='buy'
            state=_state(order) if valid else 'unknown'
            issue = '委托关联缺失或账户/股票不一致' if a.entry_order_id is not None and not valid else ''
            if a.state in ('filled','exited') and state!='filled': issue='声称成交，但缺少有效买入成交单'
            if state=='filled' and a.state not in ('filled','exited'): issue='执行留痕与成交单状态不一致'
            attempt_rows.append(dict(id=a.id,date=a.trade_date,symbol=a.symbol,state=a.state,
                order_id=a.entry_order_id,order_state=state,reason=a.reason,issue=issue,
                decision_id=a.decision_id,snapshot_id=a.snapshot_id))
        order_rows=[dict(id=o.id,scope=o.scope,symbol=o.symbol,side=o.side,
            submitted_at=to_beijing(o.created_at.replace(tzinfo=timezone.utc) if o.created_at.tzinfo is None else o.created_at).isoformat(),
            state=_state(o),price=_finite(o.price),filled_price=o.filled_price if _state(o)=='filled' else None,
            quantity=o.quantity,fee=o.fee if _state(o)=='filled' else None,reason=o.reason or '') for o in orders]
    accounts=[]
    for scope,label in zip(SCOPES,('每日精选验证','机会执行验证')):
        group=[o for o in order_rows if o['scope']==scope]
        accounts.append(dict(scope=scope,label=label,orders=len(group),
            **{state:sum(o['state']==state for o in group) for state in ('filled','pending','rejected','cancelled','unknown')}))
    runs=[]
    for n in range((last-first).days+1):
        day=(first+timedelta(days=n)).isoformat()
        try:
            log=read_log(day)
        except (OSError,ValueError,TypeError):
            log=None
        valid = isinstance(log,dict) and log.get('day')==day
        try:
            at=datetime.fromisoformat(str(log.get('executed_at'))) if valid else None
            executed = bool(at and at.tzinfo and at.astimezone(BJ_TZ).date()==date.fromisoformat(day) and isinstance(log.get('bought'),list))
        except (ValueError,TypeError):
            executed=False
        skip = log.get('skipped_reason') if valid else None
        reason = '有运行留痕，成交以委托单为准' if executed else str(skip or '无可读运行留痕，不能判断是否执行')
        runs.append(dict(date=day,state='executed' if executed else 'skipped' if skip else 'unknown',
            reason=reason,
            pick_date=log.get('pick_date') if valid else None))
    return dict(start=start,end=end,accounts=accounts,orders=order_rows,attempts=attempt_rows,runs=runs,
        basis='只读内部模拟：委托按提交日统计，执行尝试单列，不相加；成交只认原委托单。每日留痕没有订单ID，不推断逐笔关联。',
        limitation='两个账户的资金与执行条件仍隔离。没有重跑模拟，没有真实买入胜率；缺失留痕不当作零成交。')
