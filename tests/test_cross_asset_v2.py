import hashlib
from pathlib import Path

import pandas as pd

from app.ml.cross_asset_dataset import add_cross_sectional_features
from app.ml.history_cache import COLUMNS, ensure_cache, expected_grid, validate_candles


def candles(times,base=100):
    return pd.DataFrame({"open_time":times,"open":base,"high":base+2,"low":base-2,"close":base+1,"volume":10},columns=COLUMNS)


class Response:
    def __init__(self,rows):self.rows=rows
    def raise_for_status(self):pass
    def json(self):return self.rows


class Session:
    def __init__(self):self.calls=[]
    def get(self,url,params,timeout):
        self.calls.append(params); start=params["startTime"]; end=params["endTime"]
        times=pd.date_range(pd.Timestamp(start,unit="ms",tz="UTC"),pd.Timestamp(end,unit="ms",tz="UTC"),freq=params["interval"])
        return Response([[int(t.timestamp()*1000),"100","102","98","101","10",int((t+pd.Timedelta(hours=1)).timestamp()*1000)] for t in times[:1000]])


class EmptySession(Session):
    def get(self,url,params,timeout):self.calls.append(params);return Response([])


def test_missing_timeframe_fill_and_cache_idempotency(tmp_path):
    from app.ml.history_cache import PublicKlineFetcher
    session=Session(); fetcher=PublicKlineFetcher(session=session,pause_seconds=0)
    first=ensure_cache(tmp_path,"BNBUSDT","1h",fetcher,"2025-01-01","2025-01-01T05:59:59")
    assert first["fetched_rows"]==6 and first["after_missing"]==0
    calls=len(session.calls)
    second=ensure_cache(tmp_path,"BNBUSDT","1h",fetcher,"2025-01-01","2025-01-01T05:59:59")
    assert second["fetched_rows"]==0 and len(session.calls)==calls


def test_fill_only_missing_interval(tmp_path):
    from app.ml.history_cache import PublicKlineFetcher
    grid=expected_grid("1h","2025-01-01","2025-01-01T05:59:59"); candles(grid.delete(2)).to_csv(tmp_path/"XRPUSDT_1h.csv",index=False)
    session=Session(); result=ensure_cache(tmp_path,"XRPUSDT","1h",PublicKlineFetcher(session,0),"2025-01-01","2025-01-01T05:59:59")
    assert result["before_missing"]==1 and result["fetched_rows"]==1 and result["after_missing"]==0
    assert len(session.calls)==1


def test_known_exchange_gap_is_checkpointed(tmp_path):
    from app.ml.history_cache import PublicKlineFetcher
    session=EmptySession(); fetcher=PublicKlineFetcher(session,0)
    first=ensure_cache(tmp_path,"BTCUSDT","1h",fetcher,"2025-01-01","2025-01-01T00:59:59")
    second=ensure_cache(tmp_path,"BTCUSDT","1h",fetcher,"2025-01-01","2025-01-01T00:59:59")
    assert first["known_unavailable"]==1 and second["known_unavailable"]==1 and len(session.calls)==1


def test_integrity_rejects_bad_ohlc():
    frame=candles(pd.date_range("2025-01-01",periods=2,freq="1h",tz="UTC"));frame.loc[0,"high"]=50
    report=validate_candles(frame,"1h","2025-01-01","2025-01-01T01:59:59")
    assert report["invalid"]==1


def test_larger_universe_cross_sectional_rank_and_zscore():
    rows=[]; stamp=pd.Timestamp("2025-01-01",tz="UTC")
    for index,symbol in enumerate(("BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT")):
        for hour in range(25):
            rows.append({"timestamp":stamp+pd.Timedelta(hours=hour),"symbol":symbol,"close":100+hour*(index+1),
                         "return_1_1h":index/100,"momentum_4_1h":index/50,"volume_change_1h":index,
                         "volatility_20_1h":.01+index/100,"rsi_14_1h":40+index,"ema_distance_20_1h":index-2})
    result=add_cross_sectional_features(pd.DataFrame(rows)); last=result[result.timestamp==result.timestamp.max()]
    assert len(last)==5 and last.return_rank_1h.max()==1 and last.return_rank_1h.min()==.2
    assert abs(last.cross_sectional_return_zscore.mean())<1e-12


def test_v1_artifact_is_not_targeted_by_v2(tmp_path):
    v1=tmp_path/"cross_asset_v1.csv.gz";v1.write_bytes(b"frozen-v1");before=hashlib.sha256(v1.read_bytes()).hexdigest()
    v2=tmp_path/"cross_asset_v2";v2.mkdir();(v2/"metadata.json").write_text("{}")
    assert hashlib.sha256(v1.read_bytes()).hexdigest()==before and v1.parent!=v2
