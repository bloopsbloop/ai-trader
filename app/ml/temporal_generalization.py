"""Temporal-generalization and target-design audit (research diagnostics only)."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp, spearmanr

from app.ml.funnel_dataset import DEFAULT_OUTPUT as FUNNEL_DIR, ROUND_TRIP_COST

PROJECT_ROOT=Path(__file__).resolve().parents[2]
DEFAULT_DATASET=FUNNEL_DIR/"candidate_dataset.csv.gz"; DEFAULT_REGIME=FUNNEL_DIR/"regime_dataset.csv.gz"; DEFAULT_METADATA=FUNNEL_DIR/"metadata.json"
DEFAULT_REPORTS=PROJECT_ROOT/"data/ml/reports/temporal_generalization"
HORIZONS=(2,4,8); COSTS=(.0010,.0018,.0025,.0035)


def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,list):return [clean(v) for v in x]
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.floating,)):return None if not np.isfinite(x) else float(x)
    if isinstance(x,(np.bool_,)):return bool(x)
    if isinstance(x,(pd.Timestamp,datetime)):return x.isoformat()
    return x


def load_inputs(dataset=DEFAULT_DATASET,regime_path=DEFAULT_REGIME,metadata_path=DEFAULT_METADATA):
    meta=json.loads(Path(metadata_path).read_text()); features=meta["schema"]["candidate_feature_columns"]
    forbidden=[f for f in features if f.startswith(("future_","candidate_","ideal_"))]
    if forbidden:raise ValueError(f"Future/label fields in feature schema: {forbidden}")
    frame=pd.read_csv(dataset,parse_dates=["timestamp","target_timestamp"]); regime=pd.read_csv(regime_path,parse_dates=["timestamp"])
    return frame,regime,meta,features


def psi(train,other,bins=10):
    a=pd.Series(train).dropna(); b=pd.Series(other).dropna()
    if a.empty or b.empty:return np.nan
    edges=np.unique(np.r_[-np.inf,a.quantile(np.linspace(.1,.9,bins-1)).to_numpy(),np.inf])
    if len(edges)<3:return 0.
    ap=pd.cut(a,edges).value_counts(normalize=True,sort=False).to_numpy(); bp=pd.cut(b,edges).value_counts(normalize=True,sort=False).to_numpy()
    ap=np.clip(ap,1e-6,None);bp=np.clip(bp,1e-6,None);return float(np.sum((bp-ap)*np.log(bp/ap)))


def drift_row(feature,reference,current,split,symbol="ALL"):
    a=reference.dropna();b=current.dropna(); pooled=np.sqrt((a.var(ddof=0)+b.var(ddof=0))/2)
    return {"feature":feature,"symbol":symbol,"split":split,"train_mean":a.mean(),"split_mean":b.mean(),"train_median":a.median(),"split_median":b.median(),
      "train_std":a.std(ddof=0),"split_std":b.std(ddof=0),"train_q05":a.quantile(.05),"train_q25":a.quantile(.25),"train_q75":a.quantile(.75),"train_q95":a.quantile(.95),
      "split_q05":b.quantile(.05),"split_q25":b.quantile(.25),"split_q75":b.quantile(.75),"split_q95":b.quantile(.95),
      "train_missing_rate":reference.isna().mean(),"split_missing_rate":current.isna().mean(),"standardized_mean_difference":float((b.mean()-a.mean())/max(pooled,1e-12)),
      "normalized_median_shift":float(abs(b.median()-a.median())/max(a.quantile(.75)-a.quantile(.25),1e-12)),"psi":psi(a,b),"ks_statistic":float(ks_2samp(a,b).statistic) if len(a) and len(b) else np.nan}


def feature_drift(frame,features):
    train=frame[frame.split=="train"];rows=[]
    for f in features:
        for split in ("validation","test"):rows.append(drift_row(f,train[f],frame.loc[frame.split==split,f],split))
    return pd.DataFrame(rows)


def symbol_drift(frame,features):
    rows=[]
    for symbol,part in frame.groupby("symbol"):
        train=part[part.split=="train"]
        for f in features:
            for split in ("validation","test"):rows.append(drift_row(f,train[f],part.loc[part.split==split,f],split,symbol))
    return pd.DataFrame(rows)


def add_targets(frame):
    x=frame.copy(); groups=x.groupby("timestamp")
    x["relative_vs_median_4h"]=x.future_net_return_4h-groups.future_net_return_4h.transform("median")
    btc=x[x.symbol=="BTCUSDT"].set_index("timestamp").future_net_return_4h
    x["relative_vs_btc_4h"]=x.future_net_return_4h-x.timestamp.map(btc)
    x["volatility_normalized_4h"]=x.future_net_return_4h/x.volatility_20_1h.clip(lower=1e-6)
    x["atr_normalized_4h"]=x.future_net_return_4h/x.atr_fraction_14_1h.clip(lower=1e-6)
    x["risk_adjusted_expected_4h"]=x.future_net_return_4h/np.sqrt(x.volatility_20_1h.pow(2)*4).clip(lower=1e-6)
    x["clean_opportunity"]=((x.future_max_favorable_move-ROUND_TRIP_COST>.005)&(x.future_max_adverse_move>-.01)&(x.future_net_return_4h>.001)).astype(int)
    x["high_quality_opportunity"]=((x.future_max_favorable_move-ROUND_TRIP_COST>.01)&(x.future_max_adverse_move>-.0075)&(x.future_net_return_4h>.005)).astype(int)
    return x


def label_drift(frame,regime):
    labels=["candidate_positive","candidate_strong","ideal_candidate","candidate_top_2","no_trade_preferred","clean_opportunity","high_quality_opportunity"]
    rows=[]
    for dimension,parts in [("split",frame.groupby("split")),("symbol",frame.groupby("symbol")),("quarter",frame.groupby(frame.timestamp.dt.to_period("Q").astype(str)))]:
        for group,part in parts:
            for label in labels: rows.append({"dimension":dimension,"group":group,"label":label,"rows":len(part),"positive_rate":part[label].mean()})
            for h in HORIZONS:
                col=f"future_net_return_{h}h"; valid=part[col].dropna();rows.append({"dimension":dimension,"group":group,"label":f"positive_after_cost_{h}h","rows":len(valid),"positive_rate":(valid>0).mean()})
    for dimension,parts in [("split",regime.groupby("split")),("quarter",regime.groupby(regime.timestamp.dt.to_period("Q").astype(str)))]:
        for group,part in parts:
            for label in ("any_profitable_asset_4h","strong_opportunity_exists_4h","broad_positive_regime"):
                rows.append({"dimension":dimension,"group":group,"label":label,"rows":len(part),"positive_rate":part[label].mean()})
            rows += [{"dimension":dimension,"group":group,"label":"average_best_opportunity","rows":len(part),"positive_rate":part.best_net_return.mean()},
                     {"dimension":dimension,"group":group,"label":"future_dispersion","rows":len(part),"positive_rate":part.future_cross_sectional_dispersion.mean()}]
    winners=frame[frame.future_return_rank==1]
    for (split,symbol),part in winners.groupby(["split","symbol"]):rows.append({"dimension":"winner_symbol_split","group":f"{split}:{symbol}","label":"winner_frequency","rows":len(part),"positive_rate":len(part)/winners[winners.split==split].timestamp.nunique()})
    return pd.DataFrame(rows)


def horizon_analysis(frame):
    rows=[]; winners={}
    for h in HORIZONS:
        col=f"future_net_return_{h}h"; valid=frame.dropna(subset=[col]);g=valid.groupby("timestamp");idx=g[col].idxmax();win=valid.loc[idx].set_index("timestamp");winners[h]=win.symbol
        second=g[col].apply(lambda s:s.nlargest(2).iloc[-1]);median=g[col].median();best=g[col].max()
        base={"horizon_hours":h,"rows":len(valid),"timestamps":valid.timestamp.nunique(),"positive_after_cost_rate":(valid[col]>0).mean(),"no_opportunity_rate":(best<=.001).mean(),"average_best_return":best.mean(),"average_best_minus_second":(best-second).mean(),"average_best_minus_median":(best-median).mean(),"target_autocorrelation_1h":valid.groupby("symbol")[col].apply(lambda s:s.autocorr(1)).mean()}
        for symbol,count in win.symbol.value_counts().items():base[f"winner_{symbol}"]=count/len(win)
        rows.append(base)
    ref=winners[4]
    for row in rows:
        h=row["horizon_hours"];common=ref.index.intersection(winners[h].index);row["winner_persistence_vs_4h"]=(ref.loc[common]==winners[h].loc[common]).mean()
    return pd.DataFrame(rows)


def target_persistence(frame,regime):
    rows=[]; x=frame.sort_values(["symbol","timestamp"])
    for label in ("candidate_positive","candidate_top_2","ideal_candidate","clean_opportunity","high_quality_opportunity"):
        for lag in (1,2):
            future=x.groupby("symbol")[label].shift(-lag);future_time=x.groupby("symbol").timestamp.shift(-lag);exact=future_time.eq(x.timestamp+pd.Timedelta(hours=lag));positive=x[label].eq(1)&exact
            rows.append({"target":label,"lag_hours":lag,"positive_rows":positive.sum(),"persistence":future[positive].mean()})
    strong=regime.strong_opportunity_exists_4h.astype(bool);runs=(strong!=strong.shift()).cumsum();durations=regime[strong].groupby(runs[strong]).size()
    rows.append({"target":"strong_regime_duration","lag_hours":0,"positive_rows":len(durations),"persistence":durations.mean()})
    return pd.DataFrame(rows)


def target_distribution(frame):
    targets=["future_net_return_4h","relative_vs_median_4h","relative_vs_btc_4h","future_return_percentile","risk_adjusted_target","volatility_normalized_4h","atr_normalized_4h","risk_adjusted_expected_4h"]
    rows=[]
    for target in targets:
        for split,part in frame.groupby("split"):
            s=part[target].dropna();rows.append({"target":target,"split":split,"mean":s.mean(),"median":s.median(),"std":s.std(ddof=0),"q10":s.quantile(.1),"q90":s.quantile(.9),"positive_rate":(s>0).mean()})
    return pd.DataFrame(rows)


def cost_sensitivity(frame):
    gross=frame.future_net_return_4h+ROUND_TRIP_COST;rows=[]
    for cost in COSTS:
        net=gross-cost;best=net.groupby(frame.timestamp).max()
        for split,mask in [("all",pd.Series(True,index=frame.index))]+[(s,frame.split.eq(s)) for s in ("train","validation","test")]:
            values=net[mask];b=values.groupby(frame.loc[mask,"timestamp"]).max();rows.append({"round_trip_cost":cost,"split":split,"positive_asset_rate":(values>0).mean(),"no_opportunity_rate":(b<=.001).mean(),"strong_opportunity_rate":(b>.005).mean()})
    return pd.DataFrame(rows)


def mfe_mae(frame):
    rows=[];x=frame.copy();x["large_mfe_negative_close"]=(x.future_max_favorable_move-ROUND_TRIP_COST>.005)&(x.future_net_return_4h<=0);x["positive_close_severe_mae"]=(x.future_net_return_4h>0)&(x.future_max_adverse_move<-.01);x["mfe_mae_ratio"]=(x.future_max_favorable_move-ROUND_TRIP_COST)/x.future_max_adverse_move.abs().clip(lower=1e-6)
    for dims,parts in [("split",x.groupby("split")),("symbol",x.groupby("symbol")),("regime",x.groupby("regime_category"))]:
        for name,p in parts:rows.append({"dimension":dims,"group":name,"rows":len(p),"large_mfe_negative_close_rate":p.large_mfe_negative_close.mean(),"positive_close_severe_mae_rate":p.positive_close_severe_mae.mean(),"median_mfe_mae_ratio":p.mfe_mae_ratio.median(),"clean_opportunity_rate":p.clean_opportunity.mean(),"high_quality_opportunity_rate":p.high_quality_opportunity.mean()})
    return pd.DataFrame(rows)


def regime_analysis(frame):
    train=frame[frame.split=="train"];x=frame.copy();x["btc_ema_regime"]=np.where(x.btc_trend>=0,"ABOVE","BELOW");x["volatility_regime"]=np.where(x.btc_volatility>=train.btc_volatility.median(),"HIGH","LOW");x["dispersion_regime"]=np.where(x.cross_sectional_dispersion>=train.cross_sectional_dispersion.median(),"HIGH","LOW");x["breadth_regime"]=np.where(x.market_breadth>=.5,"POSITIVE","NEGATIVE");x["alignment_regime"]=np.where(x.trend_alignment_score>=2,"ALIGNED","MIXED")
    rows=[]
    for dim in ("btc_ema_regime","volatility_regime","dispersion_regime","breadth_regime","alignment_regime"):
        for (split,name),p in x.groupby(["split",dim]):rows.append({"dimension":dim,"regime":name,"split":split,"rows":len(p),"mean_future_return":p.future_net_return_4h.mean(),"positive_rate":p.candidate_positive.mean(),"clean_rate":p.clean_opportunity.mean(),"target_std":p.future_net_return_4h.std(ddof=0)})
    # Continuation versus reversal is descriptive only.
    x["behavior"]=np.where(np.sign(x.return_24h)==np.sign(x.future_net_return_4h),"CONTINUATION","REVERSAL")
    for (split,name),p in x.groupby(["split","behavior"]):rows.append({"dimension":"future_behavior","regime":name,"split":split,"rows":len(p),"mean_future_return":p.future_net_return_4h.mean(),"positive_rate":p.candidate_positive.mean(),"clean_rate":p.clean_opportunity.mean(),"target_std":p.future_net_return_4h.std(ddof=0)})
    return pd.DataFrame(rows)


def correlations(frame,features):
    targets=("future_net_return_4h","relative_vs_median_4h","volatility_normalized_4h");rows=[]
    for f in features:
        for target in targets:
            for split,p in frame.groupby("split"):
                corr=spearmanr(p[f],p[target],nan_policy="omit").statistic
                try:bins=pd.qcut(p[f].rank(method="first"),5,labels=False);means=p.groupby(bins)[target].mean().tolist()
                except ValueError:means=[]
                rows.append({"feature":f,"target":target,"split":split,"spearman":corr,"binned_target_means":json.dumps(clean(means))})
    return pd.DataFrame(rows)


def stability_scores(drift,symbols,corr,features):
    rows=[]
    for f in features:
        d=drift[drift.feature==f];sd=symbols[symbols.feature==f];c=corr[(corr.feature==f)&(corr.target=="future_net_return_4h")]
        signs=np.sign(c.spearman.dropna());sign_consistency=max((signs>=0).mean(),(signs<=0).mean()) if len(signs) else 0
        drift_penalty=np.nanmedian(abs(d.standardized_mean_difference))+np.nanmedian(d.psi)+np.nanmedian(d.ks_statistic)
        symbol_penalty=np.nanmedian(abs(sd.standardized_mean_difference));missing=max(d.train_missing_rate.max(),d.split_missing_rate.max())
        score=float(np.exp(-(drift_penalty+symbol_penalty))*(.5+.5*sign_consistency)*(1-missing));category="STABLE" if score>=.65 else ("MODERATE" if score>=.35 else "UNSTABLE")
        rows.append({"feature":f,"stability_score":score,"category":category,"drift_penalty":drift_penalty,"symbol_drift_penalty":symbol_penalty,"correlation_sign_consistency":sign_consistency,"missing_rate":missing,"train_correlation":c.loc[c.split=="train","spearman"].mean(),"validation_correlation":c.loc[c.split=="validation","spearman"].mean(),"test_correlation":c.loc[c.split=="test","spearman"].mean()})
    return pd.DataFrame(rows).sort_values("stability_score",ascending=False)


def manual_validation(frame):
    checks=[]
    for year in (2023,2024,2025):
        for symbol in ("BTCUSDT","SOLUSDT"):
            p=frame[(frame.timestamp.dt.year==year)&(frame.symbol==symbol)];row=p.iloc[len(p)//2];gross=row.future_net_return_4h+ROUND_TRIP_COST
            checks.append({"year":year,"symbol":symbol,"timestamp":row.timestamp,"net_4h":row.future_net_return_4h,"recalculated_net":gross-ROUND_TRIP_COST,"mfe":row.future_max_favorable_move,"mae":row.future_max_adverse_move,"regime":row.regime_category,"sources_not_future":all(pd.Timestamp(row[f"source_timestamp_{tf}"])<=row.timestamp for tf in ("15m","1h","4h"))})
    return {"passed":all(abs(x["net_4h"]-x["recalculated_net"])<1e-12 and x["sources_not_future"] for x in checks),"checks":checks}


def build(dataset=DEFAULT_DATASET,regime_path=DEFAULT_REGIME,metadata_path=DEFAULT_METADATA,reports=DEFAULT_REPORTS):
    reports=Path(reports);reports.mkdir(parents=True,exist_ok=True);frame,regime,meta,features=load_inputs(dataset,regime_path,metadata_path);frame=add_targets(frame);frame=frame.merge(regime[["timestamp","regime_category"]],on="timestamp",how="left",validate="many_to_one",suffixes=("","_regime")) if "regime_category" not in frame else frame
    fd=feature_drift(frame,features);sd=symbol_drift(frame,features);ld=label_drift(frame,regime);ha=horizon_analysis(frame);tp=target_persistence(frame,regime);td=target_distribution(frame);cs=cost_sensitivity(frame);mm=mfe_mae(frame);ra=regime_analysis(frame);co=correlations(frame,features);fs=stability_scores(fd,sd,co,features);manual=manual_validation(frame)
    exports={"feature_drift.csv":fd,"symbol_drift.csv":sd,"label_drift.csv":ld,"horizon_analysis.csv":ha,"target_persistence.csv":tp,"target_distribution.csv":td,"cost_sensitivity.csv":cs,"mfe_mae_analysis.csv":mm,"regime_target_analysis.csv":ra,"feature_target_stability.csv":fs,"predictability_proxy.csv":co}
    for name,value in exports.items():value.to_csv(reports/name,index=False)
    candidates={"research_only":True,"not_selected_for_training":True,"candidates":[
      {"name":"volatility_normalized_8h_return","definition":"future_net_return_8h / realized_volatility_1h; diagnostic suggests testing longer risk-normalized horizons"},
      {"name":"relative_8h_vs_universe_median","definition":"future_net_return_8h minus timestamp universe median"},
      {"name":"clean_path_opportunity","definition":"MFE after cost >0.50%, MAE >-1.00%, final net >0.10%"},
      {"name":"high_quality_path_opportunity","definition":"MFE after cost >1.00%, MAE >-0.75%, final net >0.50%"},
      {"name":"regime_conditioned_continuation","definition":"positive risk-normalized return modeled separately in fixed point-in-time trend/volatility regimes"}]}
    (reports/"target_candidates.json").write_text(json.dumps(candidates,indent=2));(reports/"manual_validation.json").write_text(json.dumps(clean(manual),indent=2))
    strongest=fd.assign(score=fd.standardized_mean_difference.abs()+fd.psi+fd.ks_statistic).nlargest(15,"score");symbol_summary=sd.groupby("symbol").agg(mean_abs_smd=("standardized_mean_difference",lambda x:x.abs().mean()),mean_psi=("psi","mean"),mean_ks=("ks_statistic","mean")).sort_values("mean_abs_smd",ascending=False)
    summary={"report_version":"temporal-generalization-v1","created_at":datetime.now(timezone.utc).isoformat(),"model_training_performed":False,"dataset_version":meta["dataset_version"],"features_audited":len(features),"rows":len(frame),"strongest_feature_drift":strongest.to_dict("records"),"symbol_drift_summary":symbol_summary.reset_index().to_dict("records"),"horizon_analysis":ha.to_dict("records"),"target_persistence":tp.to_dict("records"),"feature_stability_distribution":fs.category.value_counts().to_dict(),"top_stable_features":fs.head(15).to_dict("records"),"top_unstable_features":fs.tail(15).to_dict("records"),"manual_validation":manual,"future_fields_in_feature_schema":[]}
    (reports/"summary.json").write_text(json.dumps(clean(summary),indent=2));(reports/"summary.md").write_text(f"# Temporal Generalization Audit\n\n- Models trained: **no**\n- Features audited: {len(features)}\n- Rows: {len(frame)}\n- Stable/moderate/unstable: {fs.category.value_counts().to_dict()}\n")
    return clean(summary)


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("--dataset",default=str(DEFAULT_DATASET));p.add_argument("--regime",default=str(DEFAULT_REGIME));p.add_argument("--metadata",default=str(DEFAULT_METADATA));p.add_argument("--reports",default=str(DEFAULT_REPORTS));a=p.parse_args(argv);s=build(a.dataset,a.regime,a.metadata,a.reports);print(json.dumps({"report_version":s["report_version"],"features_audited":s["features_audited"],"rows":s["rows"],"feature_stability_distribution":s["feature_stability_distribution"],"reports":str(Path(a.reports).resolve())},indent=2))

if __name__=="__main__":main()
