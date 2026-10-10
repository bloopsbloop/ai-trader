"""M16.2 native XGBoost learning-to-rank controlled comparison."""
from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from xgboost import XGBRanker, XGBRegressor

from app.ml.ranking_objective import ROOT, DATASET, METADATA, grouped_boundaries, load_data, train_frequency_baselines
from app.ml.ranking_review import corrected_block_bootstrap

REPORTS=ROOT/"data/ml/reports/m16_2_native_ltr_v1";MODELS=ROOT/"data/models/m16_2_native_ltr_v1";SEED=42
COMMON=dict(tree_method="hist",device="cpu",n_jobs=4,random_state=SEED,n_estimators=140,learning_rate=.05,
            subsample=1.,colsample_bytree=1.,reg_lambda=1.,reg_alpha=0.,min_child_weight=1.,gamma=0.,max_bin=256,verbosity=0)
CONFIGS={"depth3":3,"depth5":5};TARGET="target_relative_vol_8h"

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def relevance(values):
    unique=np.sort(pd.unique(values));mapping={value:i for i,value in enumerate(unique)}
    return np.array([mapping[v] for v in values],dtype=np.int32)
def ordered(frame): return frame.sort_values(["timestamp","symbol"],kind="stable").reset_index(drop=True)
def qids(frame): return pd.factorize(frame.timestamp,sort=False)[0].astype(np.int32)
def assert_schema(frame,features):
    forbidden={"timestamp","symbol",TARGET,"future_net_return_8h","target_timestamp_8h"}
    if len(features)!=86 or forbidden.intersection(features): raise ValueError("invalid predictor schema")
    if frame[features].isna().any().any(): raise ValueError("missing predictors")
def labels(frame): return np.concatenate([relevance(g[TARGET].to_numpy()) for _,g in frame.groupby("timestamp",sort=False)])

def fit_model(frame,features,objective,config_id):
    x=ordered(frame);assert_schema(x,features);params={**COMMON,"max_depth":CONFIGS[config_id]}
    if objective=="ranking":
        model=XGBRanker(objective="rank:ndcg",ndcg_exp_gain=False,lambdarank_pair_method="topk",lambdarank_num_pair_per_sample=6,**params)
        model.fit(x[features],labels(x),qid=qids(x),verbose=False)
    else:
        model=XGBRegressor(objective="reg:squarederror",**params);model.fit(x[features],x[TARGET],verbose=False)
    return model

def score_frame(model,frame,features):
    x=frame.copy();x["score"]=model.predict(x[features]);return x
def evaluation(scored):
    rows=[]
    for timestamp,g in scored.groupby("timestamp",sort=True):
        prediction=g.sort_values(["score","symbol"],ascending=[False,True],kind="stable");actual=g[TARGET].max();winners=set(g.loc[g[TARGET]==actual,"symbol"]);symbols=prediction.symbol.tolist();first=min(i+1 for i,s in enumerate(symbols) if s in winners)
        rel=pd.Series(relevance(g[TARGET].to_numpy()),index=g.index);ideal=np.sort(rel.to_numpy())[::-1];pred=rel.loc[prediction.index].to_numpy()
        def ndcg(k):
            k=min(k,len(g));discount=1/np.log2(np.arange(2,k+2));den=float((ideal[:k]*discount).sum());return float((pred[:k]*discount).sum()/den) if den else 1.
        corr=spearmanr(g.score,g[TARGET]).statistic if g.score.nunique()>1 and g[TARGET].nunique()>1 else np.nan
        chosen=prediction.iloc[0];rows.append({"timestamp":timestamp,"top_1":float(first==1),"top_2":float(first<=2),"top_3":float(first<=3),"mrr":1/first,"spearman":corr,"ndcg_at_1":ndcg(1),"ndcg_at_3":ndcg(3),"raw_winner_hit":float(chosen.future_net_return_8h==g.future_net_return_8h.max()),"selected_symbol":chosen.symbol,"selected_return":chosen.future_net_return_8h,"universe":len(g),"winner_count":len(winners)})
    per=pd.DataFrame(rows);metrics={k:float(per[k].mean()) for k in ["top_1","top_2","top_3","mrr","spearman","ndcg_at_1","ndcg_at_3","raw_winner_hit"]};metrics["groups"]=len(per);return metrics,per
def select_models(train,validation,features):
    rows=[];models={}
    for objective in ("ranking","regression"):
        for config_id in CONFIGS:
            model=fit_model(train,features,objective,config_id);m,_=evaluation(score_frame(model,validation,features));rows.append({"objective":objective,"config_id":config_id,**m});models[(objective,config_id)]=model
    selected={}
    for objective in ("ranking","regression"):
        choices=[r for r in rows if r["objective"]==objective];best=max(choices,key=lambda r:(r["top_1"],r["ndcg_at_3"],-CONFIGS[r["config_id"]]));selected[objective]=(best,models[(objective,best["config_id"])])
    return pd.DataFrame(rows),selected
def save_model(model,path,meta):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);model.save_model(path);payload={**meta,"model_sha256":sha(path)};path.with_suffix(".metadata.json").write_text(json.dumps(payload,indent=2,default=str));return payload

def schedule(scored,method,exposure=.1):
    equity=peak=1000.;available=scored.timestamp.min();ledger=[]
    for timestamp,g in scored.groupby("timestamp",sort=True):
        if timestamp<available:continue
        row=g.sort_values(["score","symbol"],ascending=[False,True],kind="stable").iloc[0];before=equity;notional=before*exposure;pnl=notional*row.future_net_return_8h;equity+=pnl;peak=max(peak,equity);available=row.target_timestamp_8h
        ledger.append({"timestamp":timestamp,"method":method,"symbol":row.symbol,"selected_return":row.future_net_return_8h,"return_minus_universe_mean":row.future_net_return_8h-g.future_net_return_8h.mean(),"pnl":pnl,"equity":equity,"notional":notional,"included_cost":notional*.0018,"closed_trade_drawdown":equity/peak-1})
    l=pd.DataFrame(ledger);wins=l.loc[l.pnl>0,"pnl"].sum();loss=l.loc[l.pnl<=0,"pnl"].sum()
    return {"method":method,"net_return":equity/1000-1,"pnl":equity-1000,"mean_selected_return":l.selected_return.mean(),"median_selected_return":l.selected_return.median(),"return_minus_universe_mean":l.return_minus_universe_mean.mean(),"profit_factor":wins/abs(loss) if loss else np.inf,"trades":len(l),"turnover":float((l.notional*2).sum()),"exposure_per_trade":exposure,"included_costs":float(l.included_cost.sum()),"negative_selection_rate":float((l.selected_return<0).mean()),"closed_trade_drawdown":float(abs(l.closed_trade_drawdown.min()))},l
def baseline_score(frame,column):
    x=frame.copy();x["score"]=x[column];return x

def symbol_context(scored):
    choices=scored.sort_values(["timestamp","score","symbol"],ascending=[True,False,True]).drop_duplicates("timestamp").copy();winner=scored.loc[scored.groupby("timestamp")[TARGET].idxmax()].symbol.value_counts(normalize=True)
    choices["winner_target"]=choices.timestamp.map(scored.groupby("timestamp")[TARGET].max());choices["winner_hit"]=(choices[TARGET]==choices.winner_target).astype(float)
    rows=[]
    for symbol,g in choices.groupby("symbol"):
        rows.append({"symbol":symbol,"selection_count":len(g),"selection_share":len(g)/len(choices),"actual_winner_frequency":winner.get(symbol,0.),"conditional_hit_rate":g.winner_hit.mean(),"mean_selected_return":g.future_net_return_8h.mean()})
    states=[]
    for state,g in scored.groupby("market_state"):
        m,_=evaluation(g);states.append({"market_state":state,"rows":len(g),**m})
    return pd.DataFrame(rows),pd.DataFrame(states)

def inference_invariance(model,frame,features):
    base=frame[["timestamp","symbol"]].copy();base["score"]=model.predict(frame[features]);base=base.set_index(["timestamp","symbol"]).sort_index()
    rows=[]
    for name,other in (("reverse",frame.iloc[::-1]),("shuffle_seed_7",frame.sample(frac=1,random_state=7)),("shuffle_seed_42",frame.sample(frac=1,random_state=42))):
        x=other[["timestamp","symbol"]].copy();x["score"]=model.predict(other[features]);x=x.set_index(["timestamp","symbol"]).sort_index();delta=(x.score-base.score).abs()
        def choices(z):return z.reset_index().sort_values(["timestamp","score","symbol"],ascending=[True,False,True]).drop_duplicates("timestamp").set_index("timestamp").symbol
        rows.append({"order":name,"mean_abs_score_change":float(delta.mean()),"max_abs_score_change":float(delta.max()),"top1_choice_change_rate":float((choices(x)!=choices(base)).mean())})
    return rows

def run(reports=REPORTS,models=MODELS):
    reports=Path(reports);models=Path(models)
    if not (reports/"protocol.json").exists():raise FileNotFoundError("protocol must be frozen before fits")
    if any(p.name!="protocol.json" for p in reports.iterdir()) or models.exists():raise FileExistsError("M16.2 namespace is not empty")
    protocol_hash=sha(reports/"protocol.json");frame,meta,features=load_data();frame=ordered(frame);assert_schema(frame,features);train=frame[frame.split=="train"];validation=frame[frame.split=="validation"];test=frame[frame.split=="test"]
    selection,selected=select_models(train,validation,features);selection.to_csv(reports/"main_selection.csv",index=False);models.mkdir(parents=True)
    metrics=[];predictions=[];per_times={};manifest=[]
    for objective,(choice,model) in selected.items():
        manifest.append(save_model(model,models/f"main_{objective}.json",{"objective":objective,"config_id":choice["config_id"],"features":features,"target":TARGET,"protocol_sha256":protocol_hash}))
        for split,part in (("train",train),("validation",validation),("test",test)):
            scored=score_frame(model,part,features);m,per=evaluation(scored);metrics.append({"objective":objective,"split":split,"config_id":choice["config_id"],**m});per_times[(objective,split)]=per
            if split=="test": scored["objective"]=objective;predictions.append(scored[["timestamp","target_timestamp_8h","symbol",TARGET,"future_net_return_8h","market_state","score","objective"]])
    frozen=joblib.load(ROOT/"data/models/nextgen_relative_8h_v1/model.joblib");frozen_scored=test.copy();frozen_scored["score"]=frozen["model"].predict(test[frozen["features"]]);fm,fp=evaluation(frozen_scored);metrics.append({"objective":"frozen_m15_3","split":"test","config_id":"frozen",**fm});per_times[("frozen_m15_3","test")]=fp
    pd.DataFrame(metrics).to_csv(reports/"ranking_metrics.csv",index=False);pd.concat(predictions).to_csv(reports/"test_predictions.csv",index=False)
    # Baselines on exactly the same timestamps.
    baseline_pt,winner_frequency=train_frequency_baselines(train,test,TARGET);baselines=[]
    for name in ("uniform","constant_train_winner","train_frequency_random"):baselines.append({"baseline":name,"top_1":baseline_pt[name].mean(),"groups":len(baseline_pt)})
    for name,column in (("momentum","momentum_4_1h"),("relative_strength","return_rank_24h")):
        m,p=evaluation(baseline_score(test,column));baselines.append({"baseline":name,**m});per_times[(name,"test")]=p
    pd.DataFrame(baselines).to_csv(reports/"baselines.csv",index=False)
    # Corrected paired uncertainty, timestamp weighted.
    rank_pt=per_times[("ranking","test")];reg_pt=per_times[("regression","test")];effects=[]
    for hours in (24,8,48):effects.append({"comparison":"ranker_minus_regressor",**corrected_block_bootstrap(rank_pt,reg_pt,block_hours=hours,reps=5000)})
    for name in ("uniform","constant_train_winner","train_frequency_random"):
        right=baseline_pt[["timestamp",name]].rename(columns={name:"top_1"});effects.append({"comparison":f"ranker_minus_{name}",**corrected_block_bootstrap(rank_pt,right,reps=5000)})
    pd.DataFrame(effects).to_csv(reports/"paired_effects.csv",index=False)
    # Walk-forward: exactly four fits per fold; save selected models and all OOS rows.
    wf_metrics=[];wf_effects=[]
    for fold,tr,va,ev in grouped_boundaries(frame):
        fold_dir=reports/f"fold_{fold}";fold_dir.mkdir();model_dir=models/f"fold_{fold}";model_dir.mkdir();table,chosen=select_models(tr,va,features);table.to_csv(fold_dir/"selection.csv",index=False);fold_predictions=[];fold_ledgers=[]
        for objective,(choice,model) in chosen.items():
            artifact=save_model(model,model_dir/f"{objective}.json",{"fold":fold,"objective":objective,"config_id":choice["config_id"],"features":features,"target":TARGET,"protocol_sha256":protocol_hash});manifest.append(artifact)
            scored=score_frame(model,ev,features);m,per=evaluation(scored);econ,ledger=schedule(scored,f"fold_{fold}_{objective}");wf_metrics.append({"fold":fold,"objective":objective,"config_id":choice["config_id"],"train_start":tr.timestamp.min(),"train_end":tr.timestamp.max(),"validation_start":va.timestamp.min(),"validation_end":va.timestamp.max(),"evaluation_start":ev.timestamp.min(),"evaluation_end":ev.timestamp.max(),"train_rows":len(tr),"validation_rows":len(va),"evaluation_rows":len(ev),**m,**{f"economic_{k}":v for k,v in econ.items() if k!="method"}});per["objective"]=objective;per.to_csv(fold_dir/f"per_timestamp_{objective}.csv",index=False);scored["objective"]=objective;fold_predictions.append(scored[["timestamp","target_timestamp_8h","symbol",TARGET,"future_net_return_8h","market_state","score","objective"]]);fold_ledgers.append(ledger)
        r=pd.read_csv(fold_dir/"per_timestamp_ranking.csv",parse_dates=["timestamp"]);g=pd.read_csv(fold_dir/"per_timestamp_regression.csv",parse_dates=["timestamp"]);effect=corrected_block_bootstrap(r,g,reps=5000);wf_effects.append({"fold":fold,**effect});pd.concat(fold_predictions).to_csv(fold_dir/"predictions.csv",index=False);pd.concat(fold_ledgers).to_csv(fold_dir/"economic_ledger.csv",index=False)
        (fold_dir/"boundaries.json").write_text(json.dumps({"train":[str(tr.timestamp.min()),str(tr.timestamp.max())],"validation":[str(va.timestamp.min()),str(va.timestamp.max())],"evaluation":[str(ev.timestamp.min()),str(ev.timestamp.max())],"protocol_sha256":protocol_hash},indent=2))
    pd.DataFrame(wf_metrics).to_csv(reports/"walk_forward.csv",index=False);pd.DataFrame(wf_effects).to_csv(reports/"walk_forward_effects.csv",index=False)
    # Common fixed schedule economics.
    economics=[];ledgers=[];economic_scored={"ranking":score_frame(selected["ranking"][1],test,features),"regression":score_frame(selected["regression"][1],test,features),"frozen_m15_3":frozen_scored,"momentum":baseline_score(test,"momentum_4_1h"),"relative_strength":baseline_score(test,"return_rank_24h")}
    for name,scored in economic_scored.items():m,l=schedule(scored,name);economics.append(m);ledgers.append(l)
    economics.append({"method":"cash","net_return":0.,"pnl":0.,"trades":0,"closed_trade_drawdown":0.});pd.DataFrame(economics).to_csv(reports/"economic_diagnostics.csv",index=False);pd.concat(ledgers).to_csv(reports/"economic_ledger.csv",index=False)
    # Paired economic selected-return effects on the same 8h schedule timestamps.
    econ_effects=[];rank_ledger=ledgers[0]
    for ledger in ledgers[1:]:
        left=rank_ledger[["timestamp","selected_return"]];right=ledger[["timestamp","selected_return"]]
        econ_effects.append({"comparison":f"ranking_minus_{ledger.method.iloc[0]}",**corrected_block_bootstrap(left,right,metric="selected_return",reps=5000)})
    pd.DataFrame(econ_effects).to_csv(reports/"economic_paired_effects.csv",index=False)
    symbols,states=symbol_context(economic_scored["ranking"]);symbols.to_csv(reports/"symbol_analysis.csv",index=False);states.to_csv(reports/"market_state_analysis.csv",index=False)
    invariance=inference_invariance(selected["ranking"][1],test,features);(reports/"inference_invariance.json").write_text(json.dumps(invariance,indent=2))
    primary=next(x for x in effects if x["comparison"]=="ranker_minus_regressor" and x["block_hours"]==24);wf=pd.DataFrame(wf_effects);positive=int((wf.point_effect>0).sum());rank_test=pd.DataFrame(metrics).query("objective=='ranking' and split=='test'").iloc[0];reg_test=pd.DataFrame(metrics).query("objective=='regression' and split=='test'").iloc[0]
    practical=rank_test.top_1-reg_test.top_1>=.02;promising=practical and primary["ci_low"]>0 and positive>=2
    status="PROMISING_RANK_SIGNAL" if promising else ("WEAK" if rank_test.top_1>reg_test.top_1 else "NO_SIGNAL")
    summary={"status":status,"protocol_sha256":protocol_hash,"xgboost_version":__import__('xgboost').__version__,"market_data_fits":16,"selected_configs":{k:v[0]["config_id"] for k,v in selected.items()},"primary_ranker_minus_regressor":primary,"ranking_metrics":metrics,"walk_forward":wf_metrics,"walk_forward_effects":wf_effects,"positive_walk_forward_effect_folds":positive,"baselines":baselines,"economics":economics,"economic_paired_effects":econ_effects,"winner_frequency_train":winner_frequency,"inference_invariance":invariance,"test_provenance":"reused research holdout","limitations":["outcome-conditioned eligibility","close-to-close fixed 8h execution","closed-trade drawdown only; no mark-to-market path","constant 0.18% costs"],"deviations":[],"artifact_models":manifest}
    (reports/"summary.json").write_text(json.dumps(summary,indent=2,default=str));(reports/"artifact_manifest.json").write_text(json.dumps({"protocol_sha256":protocol_hash,"models":manifest,"files":[str(p.relative_to(ROOT)) for p in reports.rglob('*') if p.is_file()]},indent=2,default=str));return summary

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("--reports",default=str(REPORTS));p.add_argument("--models",default=str(MODELS));a=p.parse_args(argv);print(json.dumps(run(a.reports,a.models),indent=2,default=str))
if __name__=="__main__":main()
