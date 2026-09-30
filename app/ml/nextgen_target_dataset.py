"""Build nextgen-target-v1 without training models or touching trading code."""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from time import perf_counter
import numpy as np
import pandas as pd

from app.ml.funnel_dataset import DEFAULT_OUTPUT as FUNNEL_DIR,ROUND_TRIP_COST

PROJECT_ROOT=Path(__file__).resolve().parents[2];HISTORY=PROJECT_ROOT/"data/history"
DEFAULT_SOURCE=FUNNEL_DIR/"candidate_dataset.csv.gz";DEFAULT_METADATA=FUNNEL_DIR/"metadata.json"
DEFAULT_STABILITY=PROJECT_ROOT/"data/ml/reports/temporal_generalization/feature_target_stability.csv"
DEFAULT_OUTPUT=PROJECT_ROOT/"data/ml/nextgen_target_v1";VERSION="nextgen-target-v1"
TARGETS=["future_gross_return_8h","future_net_return_8h","future_mfe_gross_8h","future_mfe_net_8h","future_mae_8h","target_relative_8h","target_relative_vol_8h","future_return_vs_btc_8h","future_percentile_8h","path_quality_score","clean_opportunity_8h","high_quality_opportunity_8h","continuation_opportunity_8h","reversal_opportunity_8h"]
REGIME_FEATURES=["behavior_regime","regime_confidence","regime_direction","market_state"]

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,list):return [clean(v) for v in x]
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.floating,)):return None if not np.isfinite(x) else float(x)
    if isinstance(x,(np.bool_,)):return bool(x)
    if isinstance(x,(pd.Timestamp,datetime)):return x.isoformat()
    return x

def load_source(path=DEFAULT_SOURCE):
    x=pd.read_csv(path,parse_dates=["timestamp","target_timestamp","source_open_time"]);return x.sort_values(["timestamp","symbol"]).reset_index(drop=True)

def add_point_in_time_regimes(frame,thresholds=None):
    x=frame.copy(); signs=[]
    for tf in ("15m","1h","4h"):signs += [np.sign(x[f"ema_distance_20_{tf}"]),np.sign(x[f"momentum_4_{tf}"])]
    direction=np.sum(np.vstack(signs),axis=0)/len(signs);x["regime_direction"]=direction
    oversold=(x.rsi_14_1h<=40)&(x.momentum_4_1h<0)&(x.bollinger_position_20_1h<=.35)
    overbought=(x.rsi_14_1h>=60)&(x.momentum_4_1h>0)&(x.bollinger_position_20_1h>=.65)
    reversal_extreme=oversold|overbought
    x["behavior_regime"]=np.select([reversal_extreme&(abs(direction)<.67),abs(direction)>=.5],["REVERSAL_BIAS","CONTINUATION_BIAS"],default="MIXED")
    extreme_strength=np.maximum(abs(x.rsi_14_1h-50)/50,abs(x.bollinger_position_20_1h-.5)*2)
    x["regime_confidence"]=np.where(x.behavior_regime=="CONTINUATION_BIAS",abs(direction),np.where(x.behavior_regime=="REVERSAL_BIAS",extreme_strength,1-abs(direction))).clip(0,1)
    if thresholds is None:
        train=x[x.split=="train"].drop_duplicates("timestamp");thresholds={"volatility_q25":float(train.btc_volatility.quantile(.25)),"volatility_q75":float(train.btc_volatility.quantile(.75))}
    high=x.btc_volatility>=thresholds["volatility_q75"];low=x.btc_volatility<=thresholds["volatility_q25"]
    trending=(x.fraction_assets_above_ema>=.7)|(x.fraction_assets_above_ema<=.3)
    x["market_state"]=np.select([high,low,trending],["HIGH_VOL","LOW_VOL","TRENDING"],default="RANGING")
    return x,thresholds

def _history(symbol,history_dir=HISTORY):
    h=pd.read_csv(Path(history_dir)/f"{symbol}_1h.csv",usecols=["open_time","high","low","close"]);h.open_time=pd.to_datetime(h.open_time,utc=True);return h.set_index("open_time").sort_index()

def add_8h_path_targets(frame,history_dir=HISTORY):
    pieces=[]
    for symbol,part in frame.groupby("symbol",sort=False):
        p=part.copy();h=_history(symbol,history_dir);opens=pd.to_datetime(p.source_open_time,utc=True)
        future_close=h.close.reindex(opens+pd.Timedelta(hours=8)).to_numpy();highs=[];lows=[]
        for step in range(1,9):
            future_open=opens+pd.Timedelta(hours=step);highs.append(h.high.reindex(future_open).to_numpy());lows.append(h.low.reindex(future_open).to_numpy())
        high=np.nanmax(np.vstack(highs),axis=0);low=np.nanmin(np.vstack(lows),axis=0)
        complete=np.isfinite(future_close)&np.isfinite(high)&np.isfinite(low)
        p["target_timestamp_8h"]=p.timestamp+pd.Timedelta(hours=8);p["future_gross_return_8h"]=np.where(complete,future_close/p.close-1,np.nan)
        p["future_net_return_8h"]=p.future_gross_return_8h-ROUND_TRIP_COST;p["future_mfe_gross_8h"]=np.where(complete,high/p.close-1,np.nan);p["future_mfe_net_8h"]=p.future_mfe_gross_8h-ROUND_TRIP_COST;p["future_mae_8h"]=np.where(complete,low/p.close-1,np.nan);pieces.append(p)
    return pd.concat(pieces).sort_values(["timestamp","symbol"]).reset_index(drop=True)

def apply_8h_purge(frame):
    x=frame.copy();validation_start=x.loc[x.split=="validation","timestamp"].min();test_start=x.loc[x.split=="test","timestamp"].min();target=x.target_timestamp_8h
    safe=((x.split=="train")&(target<validation_start))|((x.split=="validation")&(target<test_start))|(x.split=="test")
    x=x[safe&x.future_net_return_8h.notna()].copy();complete=x.groupby("timestamp").future_net_return_8h.transform("count").eq(x.groupby("timestamp").symbol.transform("size"));return x[complete].reset_index(drop=True)

def add_relative_and_path_targets(frame):
    x=frame.copy();g=x.groupby("timestamp");median=g.future_net_return_8h.transform("median");x["target_relative_8h"]=x.future_net_return_8h-median
    denominator=(x.volatility_20_1h*np.sqrt(8)).clip(lower=1e-6);x["normalization_volatility_at_t"]=denominator;x["target_relative_vol_8h"]=x.target_relative_8h/denominator
    btc=x[x.symbol=="BTCUSDT"].set_index("timestamp").future_net_return_8h;x["future_return_vs_btc_8h"]=x.future_net_return_8h-x.timestamp.map(btc)
    x["future_percentile_8h"]=g.future_net_return_8h.rank(method="average",pct=True)
    x["path_quality_score"]=x.future_net_return_8h+.5*x.future_mfe_net_8h.clip(lower=0)-.5*x.future_mae_8h.clip(upper=0).abs()
    x["clean_opportunity_8h"]=((x.future_mfe_net_8h>.005)&(x.future_mae_8h>-.01)&(x.future_net_return_8h>.001)).astype(int)
    x["high_quality_opportunity_8h"]=((x.future_mfe_net_8h>.01)&(x.future_mae_8h>-.0075)&(x.future_net_return_8h>.005)).astype(int)
    bullish=x.regime_direction>=.5;oversold=(x.rsi_14_1h<=40)&(x.momentum_4_1h<0)
    x["continuation_opportunity_8h"]=((x.behavior_regime=="CONTINUATION_BIAS")&bullish&(x.future_net_return_8h>.001)&(x.future_mae_8h>-.01)).astype(int)
    x["reversal_opportunity_8h"]=((x.behavior_regime=="REVERSAL_BIAS")&oversold&(x.future_mfe_net_8h>.005)&(x.future_net_return_8h>.001)&(x.future_mae_8h>-.01)).astype(int)
    return x

def validate(frame,features,stable):
    issues=[];forbidden=[c for c in features+stable if c.startswith("future_") or c in TARGETS]
    if forbidden:issues.append("target_in_features")
    if frame.groupby("timestamp").split.nunique().max()!=1:issues.append("timestamp_crosses_split")
    if not (frame.target_timestamp_8h-frame.timestamp).eq(pd.Timedelta(hours=8)).all():issues.append("invalid_8h_horizon")
    for split,next_start in (("train",frame.loc[frame.split=="validation","timestamp"].min()),("validation",frame.loc[frame.split=="test","timestamp"].min())):
        if (frame.loc[frame.split==split,"target_timestamp_8h"]>=next_start).any():issues.append(f"{split}_purge_failure")
    if frame[features+REGIME_FEATURES].isna().any().any():issues.append("missing_features")
    return {"passed":not issues,"issues":issues,"future_fields_in_features":forbidden}

def target_stability(frame):
    continuous=["future_net_return_4h","future_net_return_8h","target_relative_8h","target_relative_vol_8h","future_return_vs_btc_8h","path_quality_score"]
    binary=["clean_opportunity_8h","high_quality_opportunity_8h","continuation_opportunity_8h","reversal_opportunity_8h"];rows=[]
    dimensions=[("split",frame.split), ("quarter",frame.timestamp.dt.tz_localize(None).dt.to_period("Q").astype(str)),("symbol",frame.symbol)]
    for dimension,series in dimensions:
        for group,p in frame.groupby(series):
            for target in continuous:
                s=p[target];rows.append({"dimension":dimension,"group":group,"target":target,"rows":len(s),"mean":s.mean(),"median":s.median(),"std":s.std(ddof=0),"q10":s.quantile(.1),"q90":s.quantile(.9),"positive_rate":(s>0).mean(),"autocorrelation_1h":p.groupby("symbol")[target].apply(lambda z:z.autocorr(1)).mean()})
            for target in binary:rows.append({"dimension":dimension,"group":group,"target":target,"rows":len(p),"mean":p[target].mean(),"median":p[target].median(),"std":p[target].std(ddof=0),"q10":p[target].quantile(.1),"q90":p[target].quantile(.9),"positive_rate":p[target].mean(),"autocorrelation_1h":p.groupby("symbol")[target].apply(lambda z:z.autocorr(1)).mean()})
    return pd.DataFrame(rows)

def symbol_balance(frame):
    labels=["clean_opportunity_8h","high_quality_opportunity_8h","continuation_opportunity_8h","reversal_opportunity_8h"];rows=[]
    for label in labels:
        rates=frame.groupby("symbol")[label].mean()
        for symbol,value in rates.items():rows.append({"target":label,"symbol":symbol,"prevalence":value,"min_prevalence":rates.min(),"max_prevalence":rates.max(),"prevalence_range":rates.max()-rates.min(),"prevalence_std":rates.std(ddof=0)})
    return pd.DataFrame(rows)

def regime_distribution(frame):
    rows=[]
    for (regime,split),p in frame.groupby(["behavior_regime","split"]):rows.append({"behavior_regime":regime,"split":split,"rows":len(p),"symbols":p.symbol.nunique(),"relative_vol_mean":p.target_relative_vol_8h.mean(),"relative_vol_std":p.target_relative_vol_8h.std(ddof=0),"clean_rate":p.clean_opportunity_8h.mean(),"high_quality_rate":p.high_quality_opportunity_8h.mean(),"continuation_positive":p.continuation_opportunity_8h.sum(),"reversal_positive":p.reversal_opportunity_8h.sum(),"mean_mfe_net":p.future_mfe_net_8h.mean(),"mean_mae":p.future_mae_8h.mean(),"mean_net_return":p.future_net_return_8h.mean()})
    return pd.DataFrame(rows)

def horizon_comparison(frame):
    rows=[]
    specs=[("4h_raw","future_net_return_4h"),("8h_raw","future_net_return_8h"),("8h_relative","target_relative_8h"),("8h_relative_vol","target_relative_vol_8h"),("8h_path_quality","path_quality_score")]
    for name,col in specs:
        for split,p in frame.groupby("split"):rows.append({"target":name,"split":split,"mean":p[col].mean(),"std":p[col].std(ddof=0),"positive_rate":(p[col]>0).mean(),"autocorrelation_1h":p.groupby("symbol")[col].apply(lambda s:s.autocorr(1)).mean()})
    return pd.DataFrame(rows)

def manual_validation(frame):
    cases={"strong_continuation":(frame.behavior_regime=="CONTINUATION_BIAS")&(frame.continuation_opportunity_8h==1),"reversal":frame.behavior_regime=="REVERSAL_BIAS","mixed":frame.behavior_regime=="MIXED","clean":frame.clean_opportunity_8h==1,"high_quality":frame.high_quality_opportunity_8h==1,"high_mfe_poor_close":(frame.future_mfe_net_8h>.005)&(frame.future_net_return_8h<=0),"severe_mae":frame.future_mae_8h<-.03};checks=[]
    for case,mask in cases.items():
        if not mask.any():checks.append({"case":case,"available":False});continue
        r=frame[mask].iloc[len(frame[mask])//2];checks.append({"case":case,"available":True,"timestamp":r.timestamp,"symbol":r.symbol,"gross_8h":r.future_gross_return_8h,"net_8h":r.future_net_return_8h,"relative":r.target_relative_8h,"denominator_at_t":r.normalization_volatility_at_t,"relative_vol":r.target_relative_vol_8h,"mfe_net":r.future_mfe_net_8h,"mae":r.future_mae_8h,"behavior_regime":r.behavior_regime,"market_state":r.market_state,"formula_ok":abs(r.target_relative_vol_8h-r.target_relative_8h/r.normalization_volatility_at_t)<1e-10})
    return {"passed":all(x.get("formula_ok",True) for x in checks),"checks":checks}

def build(source=DEFAULT_SOURCE,metadata_path=DEFAULT_METADATA,stability_path=DEFAULT_STABILITY,output_dir=DEFAULT_OUTPUT,history_dir=HISTORY):
    started=perf_counter();out=Path(output_dir);out.mkdir(parents=True,exist_ok=True);meta=json.loads(Path(metadata_path).read_text());features=meta["schema"]["candidate_feature_columns"]
    stability=pd.read_csv(stability_path);stable=[f for f in features if stability.set_index("feature").category.to_dict().get(f)!="UNSTABLE"]
    x=load_source(source);x,market_thresholds=add_point_in_time_regimes(x);x=add_8h_path_targets(x,history_dir);x=apply_8h_purge(x);x=add_relative_and_path_targets(x);safety=validate(x,features,stable)
    if not safety["passed"]:raise ValueError(safety)
    ts=target_stability(x);sb=symbol_balance(x);rd=regime_distribution(x);hc=horizon_comparison(x);manual=manual_validation(x)
    feature_meta=stability[stability.feature.isin(features)].copy();feature_meta["included_in_stable_set"]=feature_meta.category!="UNSTABLE"
    metadata_cols=["timestamp","source_open_time","target_timestamp_8h","symbol","split","eligible_universe_size","close"]
    export_cols=metadata_cols+features+REGIME_FEATURES+["normalization_volatility_at_t"]+TARGETS+["future_net_return_4h","future_return_rank"]
    x[export_cols].to_csv(out/"dataset.csv.gz",index=False,compression="gzip");ts.to_csv(out/"target_stability.csv",index=False);sb.to_csv(out/"symbol_balance.csv",index=False);rd.to_csv(out/"regime_distribution.csv",index=False);rd.to_csv(out/"continuation_reversal.csv",index=False);feature_meta.to_csv(out/"feature_stability.csv",index=False);hc.to_csv(out/"horizon_comparison.csv",index=False);x[export_cols].head(25).to_csv(out/"sample_rows.csv",index=False);(out/"manual_validation.json").write_text(json.dumps(clean(manual),indent=2))
    periods={s:{"start":p.timestamp.min(),"end":p.timestamp.max(),"rows":len(p),"timestamps":p.timestamp.nunique()} for s,p in x.groupby("split")}
    metadata={"dataset_version":VERSION,"created_at":datetime.now(timezone.utc).isoformat(),"research_only":True,"model_training_performed":False,"source_dataset":"funnel-dataset-v1","universe":sorted(x.symbol.unique()),"decision_timeframe":"1h","feature_timeframes":["15m","1h","4h"],"horizon_hours":8,"round_trip_cost":ROUND_TRIP_COST,
      "target_formulas":{"future_gross_return_8h":"close(T+8h)/close(T)-1","future_net_return_8h":"gross_8h-0.0018","target_relative_8h":"net_8h - median universe net_8h at T","target_relative_vol_8h":"target_relative_8h / max(volatility_20_1h(T)*sqrt(8),1e-6)","path_quality_score":"net_8h + 0.5*max(MFE_net,0) - 0.5*abs(min(MAE,0))"},
      "path_labels":{"clean_opportunity_8h":"MFE net >0.50%, MAE >-1.00%, final net >0.10%","high_quality_opportunity_8h":"MFE net >1.00%, MAE >-0.75%, final net >0.50%"},
      "regime_rules":{"CONTINUATION_BIAS":"absolute mean sign across EMA and momentum on 15m/1h/4h >=0.5 unless weak-trend reversal extreme","REVERSAL_BIAS":"RSI/Bollinger overbought or oversold with matching momentum and abs direction <0.67","MIXED":"otherwise","market_state":"HIGH_VOL/LOW_VOL from Train BTC-vol q75/q25; otherwise TRENDING at breadth extremes, else RANGING","thresholds":market_thresholds},
      "schemas":{"full_features":features,"full_feature_count":len(features),"stability_filtered_features":stable,"stability_filtered_feature_count":len(stable),"regime_features":REGIME_FEATURES,"target_columns":TARGETS,"metadata_columns":metadata_cols},"split_rule":"inherited grouped chronological splits with target_timestamp_8h strictly before next split; exact complete 8h path required","periods":periods,"rows":len(x),"timestamps":x.timestamp.nunique(),"target_prevalence":{t:float(x[t].mean()) for t in TARGETS if x[t].dropna().isin([0,1]).all()},"behavior_regime_distribution":x.behavior_regime.value_counts().to_dict(),"market_state_distribution":x.market_state.value_counts().to_dict(),"safety_validation":safety,"manual_validation":manual,"build_runtime_seconds":perf_counter()-started,"limitations":["8h MFE/MAE use hourly candle highs/lows and cannot identify intrabar order of adverse/favorable moves.","Relative-to-median target is cross-sectionally centered by construction.","Continuation/reversal rules are deterministic research metadata, not learned regimes."]}
    (out/"metadata.json").write_text(json.dumps(clean(metadata),indent=2));return clean(metadata)

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("--source",default=str(DEFAULT_SOURCE));p.add_argument("--metadata",default=str(DEFAULT_METADATA));p.add_argument("--stability",default=str(DEFAULT_STABILITY));p.add_argument("--output-dir",default=str(DEFAULT_OUTPUT));p.add_argument("--history-dir",default=str(HISTORY));a=p.parse_args(argv);m=build(a.source,a.metadata,a.stability,a.output_dir,a.history_dir);print(json.dumps({"dataset_version":m["dataset_version"],"rows":m["rows"],"timestamps":m["timestamps"],"features":m["schemas"]["full_feature_count"],"stable_features":m["schemas"]["stability_filtered_feature_count"],"runtime_seconds":m["build_runtime_seconds"],"output":str(Path(a.output_dir).resolve())},indent=2))
if __name__=="__main__":main()
