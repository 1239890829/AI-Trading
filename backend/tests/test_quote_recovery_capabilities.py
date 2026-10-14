"""Mode-specific recovery capability never changes ordinary fallback behavior."""
import asyncio
from datetime import datetime, timezone
from app.data_providers.composite import CompositeProvider
from app.data_providers.ths import ThsFuyaoProvider
from app.schemas.market import Quote
import pytest

@pytest.mark.parametrize('method',['get_quotes','get_indices'])
def test_explicit_thscodes_without_source_clock_does_not_spend_recovery(method):
    async def run():
        calls=[]
        ths=ThsFuyaoProvider.__new__(ThsFuyaoProvider)
        async def get(path,params):
            calls.append((path,params))
            return {'timestamp':None,'item':[{'thscode':'600001.SH','last_price':10,'prev_price':10}]}
        async def names(symbols): pass
        ths._get=get;ths._ensure_names=names;ths._names={}
        class Primary:
            name='primary'
            async def get_quotes(self,symbols): raise RuntimeError('offline primary failure')
            async def get_indices(self): raise RuntimeError('offline primary failure')
        provider=CompositeProvider([Primary(),ths])
        result=await provider.recover_quote_regressions(method,{('600001','SH'):'primary'},
            deadline=asyncio.get_running_loop().time()+4,validate=lambda q,key:None)
        assert result=={}
        assert calls==[],calls
        # Exact same method remains a normal fallback, despite missing source clock.
        rows=await (provider.get_quotes(['600001']) if method=='get_quotes' else provider.get_indices())
        assert len(calls)==1 and 'thscodes' in calls[0][1]
        assert rows[0].source=='ths' and rows[0].data_timestamp is None
    asyncio.run(run())


def test_unknown_capability_still_tries_and_explicit_opt_in_still_recovers():
    async def run():
        for declared in (None,True):
            calls=[]
            class Primary:
                name='primary'
                async def get_quotes(self,symbols): return []
            class Backup:
                name='alternative'
                if declared is not None: source_clock_recovery={'get_quotes':declared}
                async def get_quotes(self,symbols):
                    calls.append(symbols)
                    return [Quote(symbol='600001',market='SH',price=10,source=self.name,
                                  data_timestamp=datetime.now(timezone.utc))]
            provider=CompositeProvider([Primary(),Backup()])
            result=await provider.recover_quote_regressions('get_quotes',{('600001','SH'):'primary'},
                deadline=asyncio.get_running_loop().time()+4,validate=lambda q,key:q)
            assert len(calls)==1 and result[('600001','SH')].source=='alternative'
    asyncio.run(run())
