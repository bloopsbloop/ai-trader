"""Idempotent Binance public-candle cache completion for historical ML research."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from app.ai.http import PublicSession
from app.ml.features import INTERVAL_MINUTES

PUBLIC_KLINES = "https://data-api.binance.vision/api/v3/klines"
COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]


def _utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def expected_grid(timeframe, start="2023-01-01", end="2025-12-31T23:59:59"):
    return pd.date_range(_utc(start), _utc(end).floor(f"{INTERVAL_MINUTES[timeframe]}min"),
                         freq=f"{INTERVAL_MINUTES[timeframe]}min")


def load_cache(path):
    if not Path(path).exists(): return pd.DataFrame(columns=COLUMNS)
    frame=pd.read_csv(path); frame["open_time"]=pd.to_datetime(frame["open_time"],utc=True)
    return frame[COLUMNS]


def validate_candles(frame, timeframe, start="2023-01-01", end="2025-12-31T23:59:59"):
    work=frame.copy(); work["open_time"]=pd.to_datetime(work["open_time"],utc=True); work=work.sort_values("open_time")
    numeric=work[["open","high","low","close","volume"]].apply(pd.to_numeric,errors="coerce")
    invalid=pd.Series((~np.isfinite(numeric.to_numpy(dtype=float))).any(axis=1),index=work.index)
    invalid|=(numeric[["open","high","low","close"]]<=0).any(axis=1)|(numeric["volume"]<0)
    invalid|=numeric["high"]<numeric[["open","close","low"]].max(axis=1)
    invalid|=numeric["low"]>numeric[["open","close","high"]].min(axis=1)
    in_period=work[(work.open_time>=_utc(start))&(work.open_time<=_utc(end))]
    present=pd.DatetimeIndex(in_period.open_time.drop_duplicates())
    missing=expected_grid(timeframe,start,end).difference(present)
    differences=present.sort_values().to_series().diff(); expected=pd.Timedelta(minutes=INTERVAL_MINUTES[timeframe])
    return {"rows":len(in_period),"first":None if in_period.empty else in_period.open_time.min().isoformat(),
            "last":None if in_period.empty else in_period.open_time.max().isoformat(),
            "duplicates":int(in_period.open_time.duplicated().sum()),"invalid":int(invalid.sum()),
            "missing":len(missing),"gaps":int((differences.dropna()>expected).sum()),
            "coverage":float(1-len(missing)/len(expected_grid(timeframe,start,end))),"missing_times":missing}


@dataclass
class PublicKlineFetcher:
    session: object | None = None
    pause_seconds: float = .03

    def __post_init__(self):
        self.session=self.session or PublicSession(); self.calls=0

    def fetch(self,symbol,timeframe,start,end):
        cursor=_utc(start); end=_utc(end); rows=[]
        while cursor<=end:
            response=self.session.get(PUBLIC_KLINES,params={"symbol":symbol,"interval":timeframe,"limit":1000,
                                      "startTime":int(cursor.timestamp()*1000),"endTime":int(end.timestamp()*1000)},timeout=(5,30))
            self.calls+=1; response.raise_for_status(); payload=response.json()
            if not payload: break
            for item in payload:
                stamp=pd.Timestamp(item[0],unit="ms",tz="UTC")
                if cursor<=stamp<=end: rows.append([stamp,*map(float,item[1:6])])
            next_cursor=pd.Timestamp(payload[-1][0],unit="ms",tz="UTC")+pd.Timedelta(minutes=INTERVAL_MINUTES[timeframe])
            if next_cursor<=cursor: raise ValueError("Public kline pagination did not advance")
            cursor=next_cursor
            if self.pause_seconds: time.sleep(self.pause_seconds)
        return pd.DataFrame(rows,columns=COLUMNS)


def _contiguous_ranges(times,timeframe):
    if len(times)==0:return []
    step=pd.Timedelta(minutes=INTERVAL_MINUTES[timeframe]); groups=(times.to_series().diff().fillna(step)!=step).cumsum()
    return [(group.iloc[0],group.iloc[-1]) for _,group in times.to_series().groupby(groups)]


def ensure_cache(history_dir,symbol,timeframe,fetcher,start="2023-01-01",end="2025-12-31T23:59:59"):
    root=Path(history_dir); path=root/f"{symbol}_{timeframe}.csv"; existing=load_cache(path); before=validate_candles(existing,timeframe,start,end)
    if before["duplicates"] or before["invalid"]: raise ValueError(f"Invalid existing cache: {path}")
    unavailable_path=root/".unavailable_candles.json"
    try: unavailable=json.loads(unavailable_path.read_text(encoding="utf-8"))
    except FileNotFoundError: unavailable={}
    key=f"{symbol}_{timeframe}"; known=pd.DatetimeIndex(pd.to_datetime(unavailable.get(key,[]),utc=True))
    requested=before["missing_times"].difference(known); fetched=[]
    for range_start,range_end in _contiguous_ranges(requested,timeframe):
        part=fetcher.fetch(symbol,timeframe,range_start,range_end)
        if not part.empty:fetched.append(part)
    if fetched:
        merged=pd.concat([existing,*fetched],ignore_index=True).drop_duplicates("open_time",keep="last").sort_values("open_time")
        check=validate_candles(merged,timeframe,start,end)
        if check["duplicates"] or check["invalid"]:raise ValueError(f"Downloaded invalid candles: {path}")
        path.parent.mkdir(parents=True,exist_ok=True); temporary=path.with_suffix(".csv.tmp")
        merged.to_csv(temporary,index=False); temporary.replace(path); existing=merged
    after=validate_candles(existing,timeframe,start,end)
    newly_unavailable=requested.intersection(after["missing_times"])
    if len(newly_unavailable):
        unavailable[key]=sorted(set(unavailable.get(key,[]))|{stamp.isoformat() for stamp in newly_unavailable})
        unavailable_path.write_text(json.dumps(unavailable,indent=2),encoding="utf-8")
    return {"symbol":symbol,"timeframe":timeframe,"path":str(path),"calls":fetcher.calls,
            "fetched_rows":sum(len(x) for x in fetched),"before_missing":before["missing"],
            "after_missing":after["missing"],"coverage":after["coverage"],"first":after["first"],"last":after["last"],
            "rows":after["rows"],"duplicates":after["duplicates"],"invalid":after["invalid"],"gaps":after["gaps"],
            "known_unavailable":len(after["missing_times"].intersection(pd.DatetimeIndex(pd.to_datetime(unavailable.get(key,[]),utc=True))))}


def fill_universe(history_dir,symbols,timeframes=("15m","1h","4h"),fetcher=None,start="2023-01-01",end="2025-12-31T23:59:59"):
    fetcher=fetcher or PublicKlineFetcher(); results=[]
    for symbol in symbols:
        for timeframe in timeframes:
            before=fetcher.calls; row=ensure_cache(history_dir,symbol,timeframe,fetcher,start,end); row["calls"]=fetcher.calls-before; results.append(row)
    return results
