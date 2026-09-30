"""Milestone 15.3: research-only next-generation 8h models and evaluation."""
from __future__ import annotations
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from scipy.stats import pearsonr,spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import (average_precision_score,brier_score_loss,f1_score,mean_absolute_error,mean_squared_error,precision_score,r2_score,recall_score,roc_auc_score)

PROJECT_ROOT=Path(__file__).resolve().parents[2];DATA_DIR=PROJECT_ROOT/"data/ml/nextgen_target_v1";DATASET=DATA_DIR/"dataset.csv.gz";METADATA=DATA_DIR/"metadata.json"
RELATIVE_REGISTRY=PROJECT_ROOT/"data/models/nextgen_relative_8h_v1";CLEAN_REGISTRY=PROJECT_ROOT/"data/models/clean_opportunity_8h_v1";CONT_REGISTRY=PROJECT_ROOT/"data/models/continuation_8h_v1";REPORTS=PROJECT_ROOT/"data/ml/reports/nextgen_8h_v1"
TARGETS=("target_relative_vol_8h","path_quality_score");REG_PARAMS={"learning_rate":.05,"max_iter":70,"max_leaf_nodes":15,"min_samples_leaf":150,"l2_regularization":1.,"early_stopping":False,"random_state":42};CLS_PARAMS={**REG_PARAMS,"max_iter":60}
STATUS_CRITERIA={"PROMISING_RESEARCH_SIGNAL":"gated Test net >0, top1 > random+3pp, Spearman >.03, >=2/3 positive walk-forward folds, largest symbol share <50%, NO_TRADE 40-95%","WEAK":"top1 > random+1pp or positive Test net or >=2 positive walk-forward folds","NO_SIGNAL":"otherwise"}

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,list):return [clean(v) for v in x]
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.floating,)):return None if not np.isfinite(x) else float(x)
    if isinstance(x,(np.bool_,)):return bool(x)
    if isinstance(x,(pd.Timestamp,datetime)):return x.isoformat()
    return x
def sha(path):
    h=hashlib.sha256();h.update(Path(path).read_bytes());return h.hexdigest()
def load_data(dataset=DATASET,metadata_path=METADATA):
    meta=json.loads(Path(metadata_path).read_text());
    if meta["dataset_version"]!="nextgen-target-v1":raise ValueError("requires nextgen-target-v1")
    x=pd.read_csv(dataset,parse_dates=["timestamp","target_timestamp_8h"]);return x,meta
def feature_sets(meta):return {"full_87":meta["schemas"]["full_features"],"stability_filtered_86":meta["schemas"]["stability_filtered_features"]}
def assert_isolation(features,meta):
    bad=set(features)&set(meta["schemas"]["target_columns"]+meta["schemas"]["metadata_columns"])
    if bad or any(c.startswith("future_") for c in features):raise ValueError(f"target leakage: {bad}")

class Regressor:
    def __init__(self,features,target,params=REG_PARAMS):self.features=list(features);self.target=target;self.params=dict(params);self.model=HistGradientBoostingRegressor(**self.params)
    def fit(self,x):self.model.fit(x[self.features],x[self.target]);return self
    def predict(self,x):return self.model.predict(x[self.features])
    def save(self,path):Path(path).parent.mkdir(parents=True,exist_ok=True);joblib.dump({"features":self.features,"target":self.target,"params":self.params,"model":self.model},path);return Path(path)
    @classmethod
    def load(cls,path):p=joblib.load(path);o=cls(p["features"],p["target"],p["params"]);o.model=p["model"];return o
class Classifier:
    def __init__(self,features,target="clean_opportunity_8h",params=CLS_PARAMS):self.features=list(features);self.target=target;self.params=dict(params);self.model=HistGradientBoostingClassifier(**self.params)
    def fit(self,x):self.model.fit(x[self.features],x[self.target]);return self
    def predict(self,x):return self.model.predict_proba(x[self.features])[:,1]
    def save(self,path):Path(path).parent.mkdir(parents=True,exist_ok=True);joblib.dump({"features":self.features,"target":self.target,"params":self.params,"model":self.model},path);return Path(path)
    @classmethod
    def load(cls,path):p=joblib.load(path);o=cls(p["features"],p["target"],p["params"]);o.model=p["model"];return o

def ranked(frame,pred,target):
    x=frame.copy();x["prediction"]=pred;g=x.groupby("timestamp");x["predicted_rank"]=g.prediction.rank(method="first",ascending=False).astype(int);x["actual_rank"]=g[target].rank(method="first",ascending=False).astype(int);return x
def _correlation(a,b,kind="spearman"):
    a=np.asarray(a);b=np.asarray(b)
    if len(a)<2 or len(np.unique(a[np.isfinite(a)]))<2 or len(np.unique(b[np.isfinite(b)]))<2:return 0.
    return float(spearmanr(a,b).statistic if kind=="spearman" else pearsonr(a,b).statistic)
def ranking_metrics(x,target):
    winners=x[x.predicted_rank==1];corr=x.groupby("timestamp").apply(lambda g:_correlation(g.prediction,g[target]),include_groups=False)
    nd1=[];nd3=[]
    for _,g in x.groupby("timestamp"):
        actual=g[target].to_numpy();rel=np.maximum(actual-actual.min(),0);order=np.argsort(-g.prediction.to_numpy());ideal=np.argsort(-rel)
        def nd(k):
            disc=1/np.log2(np.arange(2,k+2));v=(rel[order[:k]]*disc).sum();best=(rel[ideal[:k]]*disc).sum();return v/best if best>0 else 1.
        nd1.append(nd(1));nd3.append(nd(min(3,len(g))))
    a=winners.actual_rank
    return {"top_1":(a==1).mean(),"top_2":(a<=2).mean(),"top_3":(a<=3).mean(),"mrr":(1/a).mean(),"spearman":corr.mean(),"ndcg_at_1":np.mean(nd1),"ndcg_at_3":np.mean(nd3),"mean_actual_rank":a.mean(),"timestamps":x.timestamp.nunique()}
def regression_metrics(y,p):
    return {"mae":mean_absolute_error(y,p),"rmse":mean_squared_error(y,p)**.5,"r2":r2_score(y,p),"pearson":_correlation(y,p,"pearson"),"spearman":_correlation(y,p),"normalized_mae":mean_absolute_error(y,p)/max(y.std(ddof=0),1e-9)}
def classifier_metrics(y,p,threshold):
    z=p>=threshold;return {"roc_auc":roc_auc_score(y,p),"pr_auc":average_precision_score(y,p),"precision":precision_score(y,z,zero_division=0),"recall":recall_score(y,z,zero_division=0),"f1":f1_score(y,z,zero_division=0),"brier":brier_score_loss(y,p),"threshold":threshold}

def simulate(x,score_threshold=-np.inf,gate_threshold=None,selector="model",seed=42,exposure=.10):
    work=x.sort_values("timestamp");equity=peak=1000.;available=pd.Timestamp.min.tz_localize("UTC");ledger=[];curve=[equity];rng=np.random.default_rng(seed);first,last=work.timestamp.min(),work.timestamp.max()
    for timestamp,g in work.groupby("timestamp",sort=True):
        if timestamp<available:continue
        if selector=="model":row=g.loc[g.prediction.idxmax()]
        elif selector=="momentum":row=g.loc[g.momentum_4_1h.idxmax()]
        elif selector=="relative_strength":row=g.loc[g.return_rank_24h.idxmax()]
        elif selector=="random":row=g.iloc[rng.integers(len(g))]
        elif selector=="oracle":row=g.loc[g.future_net_return_8h.idxmax()]
        elif selector=="ranker_v2":row=g.loc[g.ranker_v2_score.idxmax()]
        else:raise ValueError(selector)
        score=row.get("prediction",np.inf)
        if selector=="model" and score<score_threshold:continue
        if gate_threshold is not None and row.clean_probability<gate_threshold:continue
        notional=equity*exposure;pnl=notional*row.future_net_return_8h;equity+=pnl;peak=max(peak,equity);curve.append(equity);available=row.target_timestamp_8h
        ledger.append({"timestamp":timestamp,"target_timestamp":row.target_timestamp_8h,"symbol":row.symbol,"prediction":row.get("prediction",np.nan),"clean_probability":row.get("clean_probability",np.nan),"actual_net_return":row.future_net_return_8h,"pnl":pnl,"equity":equity,"actual_rank":row.actual_rank,"mfe_net":row.future_mfe_net_8h,"mae":row.future_mae_8h,"path_quality_score":row.path_quality_score})
    wins=[r["pnl"] for r in ledger if r["pnl"]>0];loss=[r["pnl"] for r in ledger if r["pnl"]<=0];dd=min((v/max(curve[:i+1])-1 for i,v in enumerate(curve)),default=0.);hours=max(1,(last-first).total_seconds()/3600);notional_turn=sum((r["equity"]-r["pnl"])*exposure*2 for r in ledger)
    possible_slots=max(1,int(np.ceil(hours/8)))
    m={"net_return":equity/1000-1,"pnl":equity-1000,"trades":len(ledger),"win_rate":len(wins)/len(ledger) if ledger else 0.,"profit_factor":sum(wins)/abs(sum(loss)) if loss and sum(loss) else (float("inf") if wins else 0.),"expectancy":np.mean([r["pnl"] for r in ledger]) if ledger else 0.,"max_drawdown":abs(dd),"fees":sum((r["equity"]-r["pnl"])*exposure*.0018 for r in ledger),"turnover":notional_turn,"time_in_market":min(1,len(ledger)*8/hours),"no_trade_rate":max(0.,1-len(ledger)/possible_slots),"ending_equity":equity}
    return clean(m),pd.DataFrame(ledger)

def select_trade_thresholds(validation_ranked,clean_prob):
    x=validation_ranked.copy();x["clean_probability"]=clean_prob;tops=x[x.predicted_rank==1];score_grid=sorted(set([float("-inf"),0.]+[float(tops.prediction.quantile(q)) for q in (.1,.25,.4,.55,.7,.85)]));gate_grid=[None,.3,.4,.5,.6,.7];rows=[]
    slots=max(1,x.timestamp.nunique()/8)
    for score in score_grid:
        for gate in gate_grid:
            m,_=simulate(x,score,gate);freq=m["trades"]/slots;eligible=50<=m["trades"] and .05<=freq<=.60;objective=m["net_return"]-.5*m["max_drawdown"] if eligible else -1e6+m["trades"]
            rows.append({"score_threshold":score,"gate_threshold":gate,"eligible":eligible,"objective":objective,**m})
    regression=max((r for r in rows if r["gate_threshold"] is None),key=lambda r:r["objective"]);gated=max((r for r in rows if r["gate_threshold"] is not None),key=lambda r:r["objective"]);return regression,gated,rows

def select_models(frame,meta):
    train=frame[frame.split=="train"];val=frame[frame.split=="validation"];rows=[]
    for target in TARGETS:
        for name,features in feature_sets(meta).items():
            assert_isolation(features,meta);model=Regressor(features,target).fit(train);tr=regression_metrics(train[target],model.predict(train));vp=model.predict(val);vr=regression_metrics(val[target],vp);rank=ranking_metrics(ranked(val,vp,target),target);rows.append({"target":target,"feature_set":name,"features":features,"model":model,"train_regression":tr,"validation_regression":vr,"validation_ranking":rank})
    winner=max(rows,key=lambda r:(r["validation_ranking"]["top_1"],r["validation_ranking"]["spearman"],r["validation_ranking"]["ndcg_at_3"],-r["validation_regression"]["mae"]));return winner,rows
def select_classifier(frame,meta):
    train=frame[frame.split=="train"];val=frame[frame.split=="validation"];rows=[]
    for name,features in feature_sets(meta).items():
        model=Classifier(features).fit(train);p=model.predict(val);rows.append({"feature_set":name,"features":features,"model":model,"validation_pr_auc":average_precision_score(val.clean_opportunity_8h,p),"validation_roc_auc":roc_auc_score(val.clean_opportunity_8h,p)})
    return max(rows,key=lambda r:(r["validation_pr_auc"],r["validation_roc_auc"])),rows

def permutation_rows(model,frame,split,fold="final"):
    sample=frame.sample(min(1200,len(frame)),random_state=42);result=permutation_importance(model.model,sample[model.features],sample[model.target],n_repeats=1,random_state=42,scoring="neg_mean_absolute_error")
    return [{"fold":fold,"split":split,"feature":f,"importance":v} for f,v in sorted(zip(model.features,result.importances_mean),key=lambda z:z[1],reverse=True)[:20]]
def walk_forward(frame,features,target):
    times=np.array(sorted(frame.timestamp.unique()));windows=[(.40,.50,.667),(.50,.667,.834),(.667,.834,1.)];rows=[];importance=[]
    for fold,(a,b,c) in enumerate(windows,1):
        ta,tb,tc=times[int(len(times)*a)],times[int(len(times)*b)],times[min(len(times)-1,int(len(times)*c)-1)];stamp=frame.timestamp;target_time=frame.target_timestamp_8h
        train=frame[(stamp<ta)&(target_time<ta)];val=frame[(stamp>=ta)&(stamp<tb)&(target_time<tb)];ev=frame[(stamp>=tb)&(stamp<=tc)]
        model=Regressor(features,target).fit(train);classifier=Classifier(features).fit(train);vr=ranked(val,model.predict(val),target);vp=classifier.predict(val);reg,gated,_=select_trade_thresholds(vr,vp);er=ranked(ev,model.predict(ev),target);er["clean_probability"]=classifier.predict(ev);rank=ranking_metrics(er,target);trade,_=simulate(er,gated["score_threshold"],gated["gate_threshold"])
        rows.append({"fold":fold,"train_rows":len(train),"validation_rows":len(val),"evaluation_rows":len(ev),"score_threshold":gated["score_threshold"],"gate_threshold":gated["gate_threshold"],**rank,**{f"trading_{k}":v for k,v in trade.items()}});importance+=permutation_rows(model,val,"validation",fold)
    return pd.DataFrame(rows),pd.DataFrame(importance)

def adaptive(frame,features,target):
    train=frame[frame.split=="train"];val=frame[frame.split=="validation"];rows=[];models={}
    for method,part in (("expanding",train),("rolling_12m",train[train.timestamp>=train.timestamp.max()-pd.Timedelta(days=365)])):
        model=Regressor(features,target).fit(part);p=model.predict(val);rank=ranking_metrics(ranked(val,p,target),target);reg=regression_metrics(val[target],p);rows.append({"method":method,"train_rows":len(part),**rank,**{f"regression_{k}":v for k,v in reg.items()}});models[method]=model
    winner=max(rows,key=lambda r:(r["top_1"],r["spearman"],r["ndcg_at_3"]));return winner["method"],pd.DataFrame(rows),models

def symbol_report(ranked_test,ledger):
    selected=ledger.symbol.value_counts() if len(ledger) else pd.Series(dtype=int);rows=[]
    for symbol,p in ranked_test.groupby("symbol"):
        trades=ledger[ledger.symbol==symbol] if len(ledger) else pd.DataFrame();rows.append({"symbol":symbol,"selection_count":int(selected.get(symbol,0)),"average_prediction":p.prediction.mean(),"actual_future_return":p.future_net_return_8h.mean(),"win_rate":(trades.actual_net_return>0).mean() if len(trades) else 0.,"pnl_contribution":trades.pnl.sum() if len(trades) else 0.,"clean_probability":p.clean_probability.mean(),"mean_actual_rank_when_selected":trades.actual_rank.mean() if len(trades) else np.nan})
    return pd.DataFrame(rows)
def regime_report(test_ranked,score_t,gate_t):
    rows=[]
    for dimension in ("behavior_regime","market_state"):
        for name,p in test_ranked.groupby(dimension):
            rank=ranking_metrics(p,pred_target(test_ranked));trade,_=simulate(p,score_t,gate_t);rows.append({"dimension":dimension,"regime":name,"rows":len(p),**rank,**{f"trading_{k}":v for k,v in trade.items()}})
    return pd.DataFrame(rows)
def pred_target(x):return x.attrs.get("target","target_relative_vol_8h")

def train_and_evaluate(dataset=DATASET,metadata_path=METADATA,reports=REPORTS,force=False):
    for registry in (RELATIVE_REGISTRY,CLEAN_REGISTRY,CONT_REGISTRY):
        if registry.exists() and any(registry.iterdir()) and not force:raise FileExistsError(registry)
    reports=Path(reports);reports.mkdir(parents=True,exist_ok=True);frame,meta=load_data(dataset,metadata_path);winner,comparisons=select_models(frame,meta);classifier_choice,classifiers=select_classifier(frame,meta);features=winner["features"];target=winner["target"]
    # Adaptive selection is Validation-only; final Test uses its selected Train window.
    window,adaptive_table,window_models=adaptive(frame,features,target);primary=window_models[window]
    train=frame[frame.split=="train"];val=frame[frame.split=="validation"];test=frame[frame.split=="test"]
    clean_model=classifier_choice["model"];val_ranked=ranked(val,primary.predict(val),target);val_clean=clean_model.predict(val);reg_threshold,gated_threshold,threshold_rows=select_trade_thresholds(val_ranked,val_clean)
    test_ranked=ranked(test,primary.predict(test),target);test_ranked.attrs["target"]=target;test_ranked["clean_probability"]=clean_model.predict(test)
    reg_trade,reg_ledger=simulate(test_ranked,reg_threshold["score_threshold"],None);gated_trade,gated_ledger=simulate(test_ranked,gated_threshold["score_threshold"],gated_threshold["gate_threshold"])
    # Continuation-only regressor, same selected target/features.
    ct=train[train.behavior_regime=="CONTINUATION_BIAS"];cv=val[val.behavior_regime=="CONTINUATION_BIAS"];ce=test[test.behavior_regime=="CONTINUATION_BIAS"]
    continuation=Regressor(features,target).fit(ct);cv_rank=ranked(cv,continuation.predict(cv),target);cv_rank["clean_probability"]=clean_model.predict(cv);cont_reg,cont_gate,_=select_trade_thresholds(cv_rank,cv_rank.clean_probability);ce_rank=ranked(ce,continuation.predict(ce),target);ce_rank["clean_probability"]=clean_model.predict(ce);cont_trade,cont_ledger=simulate(ce_rank,cont_gate["score_threshold"],cont_gate["gate_threshold"])
    # Save separate research artifacts.
    for registry in (RELATIVE_REGISTRY,CLEAN_REGISTRY,CONT_REGISTRY):registry.mkdir(parents=True,exist_ok=True)
    relative_path=primary.save(RELATIVE_REGISTRY/"model.joblib");clean_path=clean_model.save(CLEAN_REGISTRY/"model.joblib");cont_path=continuation.save(CONT_REGISTRY/"model.joblib")
    ranking_rows=[];reg_rows=[]
    for split,p in (("train",train),("validation",val),("test",test)):
        pred=primary.predict(p);rank=ranking_metrics(ranked(p,pred,target),target);ranking_rows.append({"split":split,**rank});reg_rows.append({"split":split,**regression_metrics(p[target],pred)})
    ranking_table=pd.DataFrame(ranking_rows);reg_table=pd.DataFrame(reg_rows)
    clean_rows=[]
    gate_threshold=gated_threshold["gate_threshold"]
    for split,p in (("train",train),("validation",val),("test",test)):clean_rows.append({"dimension":"split","group":split,**classifier_metrics(p.clean_opportunity_8h,clean_model.predict(p),gate_threshold)})
    for symbol,p in test.groupby("symbol"):clean_rows.append({"dimension":"test_symbol","group":symbol,**classifier_metrics(p.clean_opportunity_8h,clean_model.predict(p),gate_threshold)})
    clean_table=pd.DataFrame(clean_rows)
    wf,wf_importance=walk_forward(frame,features,target)
    # Baselines on same 8h non-overlap rule.
    simulations=[{"method":"relative_regression",**reg_trade},{"method":"relative_plus_clean_gate",**gated_trade},{"method":"continuation_pipeline",**cont_trade},{"method":"cash","net_return":0.,"pnl":0.,"trades":0}]
    for method,selector in (("momentum","momentum"),("relative_strength","relative_strength"),("random_seed_42","random"),("oracle_impossible","oracle")):
        m,_=simulate(test_ranked,selector=selector);simulations.append({"method":method,**m})
    score_path=PROJECT_ROOT/"data/ml/reports/opportunity_ranker_v2/test_rankings.csv"
    if score_path.exists():
        scores=pd.read_csv(score_path,usecols=["timestamp","symbol","predicted_net_return"]);scores.timestamp=pd.to_datetime(scores.timestamp,utc=True);joined=test_ranked.merge(scores,on=["timestamp","symbol"],how="left").rename(columns={"predicted_net_return":"ranker_v2_score"});m,_=simulate(joined,selector="ranker_v2");simulations.append({"method":"frozen_opportunity_ranker_v2",**m})
    sim_table=pd.DataFrame(simulations)
    symbols=symbol_report(test_ranked,gated_ledger);regimes=regime_report(test_ranked,gated_threshold["score_threshold"],gate_threshold)
    path_rows=[]
    for name,ledger in (("regression",reg_ledger),("gated",gated_ledger),("continuation",cont_ledger)):
        path_rows.append({"pipeline":name,"trades":len(ledger),"mean_mfe_net":ledger.mfe_net.mean() if len(ledger) else np.nan,"mean_mae":ledger.mae.mean() if len(ledger) else np.nan,"mean_final_net":ledger.actual_net_return.mean() if len(ledger) else np.nan,"mean_path_quality":ledger.path_quality_score.mean() if len(ledger) else np.nan,"high_mfe_bad_final_rate":((ledger.mfe_net>.005)&(ledger.actual_net_return<=0)).mean() if len(ledger) else np.nan,"severe_mae_rate":(ledger.mae<-.01).mean() if len(ledger) else np.nan})
    path_table=pd.DataFrame(path_rows)
    importance=pd.concat([pd.DataFrame(permutation_rows(primary,val,"validation","final")),wf_importance],ignore_index=True)
    errors=test_ranked[(test_ranked.predicted_rank==1)&(test_ranked.actual_rank>4)][["timestamp","symbol","prediction",target,"future_net_return_8h","clean_probability","actual_rank","behavior_regime","market_state"]]
    largest_share=symbols.selection_count.max()/max(1,symbols.selection_count.sum());positive_folds=(wf.trading_net_return>0).sum();test_rank=ranking_table.set_index("split").loc["test"]
    promising=gated_trade["net_return"]>0 and test_rank.top_1>1/7+.03 and test_rank.spearman>.03 and positive_folds>=2 and largest_share<.5 and .4<=gated_trade["no_trade_rate"]<=.95
    weak=test_rank.top_1>1/7+.01 or gated_trade["net_return"]>0 or positive_folds>=2;status="PROMISING_RESEARCH_SIGNAL" if promising else ("WEAK" if weak else "NO_SIGNAL")
    # Reports.
    comparison_rows=[]
    for r in comparisons:comparison_rows.append({"target":r["target"],"feature_set":r["feature_set"],**{f"validation_{k}":v for k,v in r["validation_ranking"].items()},**{f"validation_regression_{k}":v for k,v in r["validation_regression"].items()}})
    pd.DataFrame(comparison_rows).to_csv(reports/"target_comparison.csv",index=False);pd.DataFrame(comparison_rows).to_csv(reports/"feature_set_comparison.csv",index=False);ranking_table.to_csv(reports/"ranking_metrics.csv",index=False);clean_table.to_csv(reports/"clean_classifier_metrics.csv",index=False);sim_table.to_csv(reports/"simulation_results.csv",index=False);wf.to_csv(reports/"walk_forward.csv",index=False);adaptive_table.to_csv(reports/"adaptive_window.csv",index=False);symbols.to_csv(reports/"symbol_results.csv",index=False);regimes.to_csv(reports/"regime_results.csv",index=False);path_table.to_csv(reports/"path_analysis.csv",index=False);importance.to_csv(reports/"feature_importance.csv",index=False);errors.to_csv(reports/"errors.csv",index=False);pd.DataFrame(threshold_rows).to_csv(reports/"threshold_sensitivity.csv",index=False);reg_table.to_csv(reports/"regression_metrics.csv",index=False)
    hashes={"relative_model":sha(relative_path),"clean_model":sha(clean_path),"continuation_model":sha(cont_path)}
    summary={"model_version":"nextgen_8h_v1","research_only":True,"not_for_batch3_control":True,"dataset_version":"nextgen-target-v1","status":status,"status_criteria":STATUS_CRITERIA,"winning_target":target,"winning_feature_set":winner["feature_set"],"adaptive_window":window,"parameters":{"regressor":REG_PARAMS,"classifier":CLS_PARAMS},"thresholds":{"regression_score":reg_threshold["score_threshold"],"gated_score":gated_threshold["score_threshold"],"clean_gate":gate_threshold},"validation_ranking":winner["validation_ranking"],"ranking_metrics":ranking_table.to_dict("records"),"regression_metrics":reg_table.to_dict("records"),"clean_classifier":clean_table.to_dict("records"),"simulations":sim_table.to_dict("records"),"walk_forward":wf.to_dict("records"),"adaptive_results":adaptive_table.to_dict("records"),"symbol_results":symbols.to_dict("records"),"regime_results":regimes.to_dict("records"),"path_analysis":path_table.to_dict("records"),"largest_symbol_selection_share":largest_share,"positive_walk_forward_folds":int(positive_folds),"test_used_for_selection":False,"hashes":hashes}
    for registry,name,path in ((RELATIVE_REGISTRY,"NEXTGEN_RELATIVE_8H_V1",relative_path),(CLEAN_REGISTRY,"CLEAN_OPPORTUNITY_8H_V1",clean_path),(CONT_REGISTRY,"CONTINUATION_8H_V1",cont_path)):
        payload={"name":name,"research_only":True,"not_for_batch3_control":True,"dataset_version":"nextgen-target-v1","model_sha256":sha(path),"features":features,"target":target if name!="CLEAN_OPPORTUNITY_8H_V1" else "clean_opportunity_8h","parameters":REG_PARAMS if name!="CLEAN_OPPORTUNITY_8H_V1" else CLS_PARAMS,"created_at":datetime.now(timezone.utc).isoformat()};(registry/"metadata.json").write_text(json.dumps(clean(payload),indent=2))
    (reports/"summary.json").write_text(json.dumps(clean(summary),indent=2));(reports/"summary.md").write_text(f"# Nextgen 8h v1\n\n- Status: **{status}**\n- Target: `{target}`\n- Features: `{winner['feature_set']}`\n- Adaptive window: `{window}`\n- Test top-1: {test_rank.top_1:.4f}\n- Gated Test net return: {gated_trade['net_return']:.4%}\n")
    return clean(summary)

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("--dataset",default=str(DATASET));p.add_argument("--metadata",default=str(METADATA));p.add_argument("--reports",default=str(REPORTS));p.add_argument("--force",action="store_true");a=p.parse_args(argv);s=train_and_evaluate(a.dataset,a.metadata,a.reports,a.force);print(json.dumps({k:s[k] for k in ("status","winning_target","winning_feature_set","adaptive_window","thresholds","validation_ranking","hashes")},indent=2))
if __name__=="__main__":main()
