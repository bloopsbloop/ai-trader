"""Final untouched-Test evaluation for research-only Opportunity Ranker v2."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd

from app.ml.cross_asset_dataset import PROJECT_ROOT
from app.ml.opportunity import (OpportunityRanker,clean,decision_distribution,per_symbol_results,ranked_predictions,ranking_metrics,regression_metrics,simulate)
from app.ml.opportunity_train_v2 import DEFAULT_DATASET,DEFAULT_METADATA,DEFAULT_REGISTRY,PARAMETERS,load_v2,select_configuration

REPORTS=PROJECT_ROOT/"data/ml/reports/opportunity_ranker_v2"


def random_reference(frame,seed=42):
    rng=np.random.default_rng(seed);ranked=ranked_predictions(frame,rng.random(len(frame)));metrics=ranking_metrics(ranked);trading,_=simulate(ranked,-np.inf)
    sizes=frame.groupby("timestamp").size().to_numpy(float)
    metrics["analytical_top_1"]=float(np.mean(1/sizes));metrics["analytical_top_2"]=float(np.mean(np.minimum(2/sizes,1)));metrics["analytical_top_3"]=float(np.mean(np.minimum(3/sizes,1)))
    return metrics,trading


def regime_analysis(ranked,threshold,volatility_cutoff):
    work=ranked.copy();work["btc_trend_regime"]=np.where(work.btc_trend>=0,"positive","negative");work["volatility_regime"]=np.where(work.btc_volatility>=volatility_cutoff,"high","low");work["breadth_regime"]=np.where(work.market_breadth>=.5,"positive","negative")
    rows=[]
    for dimension in ("btc_trend_regime","volatility_regime","breadth_regime"):
        for value,group in work.groupby(dimension):
            ranking=ranking_metrics(group);trading,_=simulate(group,threshold)
            rows.append({"dimension":dimension,"regime":value,"timestamps":group.timestamp.nunique(),"top_1_accuracy":ranking["top_1_accuracy"],"top_3_accuracy":ranking["top_3_accuracy"],"spearman":ranking["average_spearman"],"net_return":trading["net_return"],"trades":trading["trades"]})
    return rows


def score_gap_analysis(ranked):
    work=ranked.copy();second=work[work.predicted_rank==2][["timestamp","predicted_net_return"]].rename(columns={"predicted_net_return":"second_prediction"});work=work.merge(second,on="timestamp",how="left");w=work[work.predicted_rank==1].copy();w["prediction_gap"]=w.predicted_net_return-w.second_prediction
    w["gap_bucket"]=pd.qcut(w.prediction_gap.rank(method="first"),4,labels=["Q1_small","Q2","Q3","Q4_large"])
    return [{"gap_bucket":str(name),"timestamps":len(group),"mean_gap":float(group.prediction_gap.mean()),"top_1_accuracy":float((group.future_return_rank==1).mean()),"mean_future_return":float(group.future_net_return_4h.mean()),"positive_rate":float((group.future_net_return_4h>0).mean())} for name,group in w.groupby("gap_bucket",observed=True)]


def opportunity_strength(ranked,validation):
    positive=validation.groupby("timestamp").best_available_net_return.first();positive=positive[positive>0];q1,q2=positive.quantile([.33,.66]);w=ranked[ranked.predicted_rank==1].copy();best=w.best_available_net_return
    w["strength"]=np.select([best<=0,best<=q1,best<=q2],["no_profitable_asset","weak","medium"],default="strong")
    return [{"strength":name,"timestamps":len(g),"top_1_accuracy":float((g.future_return_rank==1).mean()),"winner_return":float(g.future_net_return_4h.mean())} for name,g in w.groupby("strength")]


def walk_forward(frame,parameters):
    times=np.array(sorted(pd.to_datetime(frame.timestamp,utc=True).unique()));n=len(times);bounds=[(.40,.50,.667),(.50,.667,.834),(.667,.834,1.)];results=[]
    fast=dict(parameters);fast["max_iter"]=20
    for fold,(a,b,c) in enumerate(bounds,1):
        ta,tb,tc=times[int(n*a)],times[int(n*b)],times[min(n-1,int(n*c)-1)];stamp=pd.to_datetime(frame.timestamp,utc=True);target=pd.to_datetime(frame.target_timestamp,utc=True)
        train=frame[(stamp<ta)&(target<ta)].copy();train["split"]="train";validation=frame[(stamp>=ta)&(stamp<tb)&(target<tb)].copy();validation["split"]="validation";selection_frame=pd.concat([train,validation])
        winner,_=select_configuration(selection_frame,{"features":json.loads(Path(DEFAULT_METADATA).read_text())["features"],"schema":json.loads(Path(DEFAULT_METADATA).read_text())["schema"]},fast,min(50,max(20,validation.timestamp.nunique()//40)))
        evaluation=frame[(stamp>=tb)&(stamp<=tc)].copy();ranked=ranked_predictions(evaluation,winner["model"].predict(evaluation));rank=ranking_metrics(ranked);trade,_=simulate(ranked,winner["threshold"]);decisions=decision_distribution(ranked,winner["threshold"]);no_trade=float(decisions.loc[decisions.selection=="NO_TRADE","percentage"].iloc[0])
        results.append({"fold":fold,"target":winner["target_label"],"feature_set":winner["feature_set"],"threshold":winner["threshold"],"top_1":rank["top_1_accuracy"],"top_3":rank["top_3_accuracy"],"spearman":rank["average_spearman"],"net_return":trade["net_return"],"trades":trade["trades"],"profit_factor":trade["profit_factor"],"max_drawdown":trade["max_drawdown"],"no_trade_rate":no_trade,"evaluation_start":str(tb),"evaluation_end":str(tc)})
    return results


def evaluate(registry=DEFAULT_REGISTRY,dataset=DEFAULT_DATASET,metadata_path=DEFAULT_METADATA,reports=REPORTS):
    registry,reports=Path(registry),Path(reports);reports.mkdir(parents=True,exist_ok=True);meta=json.loads((registry/"metadata.json").read_text());frame,dataset_meta=load_v2(dataset,metadata_path);test=frame[frame.split=="test"].copy();validation=frame[frame.split=="validation"]
    model=OpportunityRanker.load(registry/"model.joblib");ranked=ranked_predictions(test,model.predict(test));rank=ranking_metrics(ranked);regression=regression_metrics(test[meta["target_column"]],ranked.predicted_net_return);threshold=meta["decision_threshold"];trading,ledger=simulate(ranked,threshold)
    candidate_rows=[]
    for candidate in meta["candidate_comparison"]:
        candidate_model=OpportunityRanker.load(registry/candidate["model_file"]);candidate_ranked=ranked_predictions(test,candidate_model.predict(test));metrics=ranking_metrics(candidate_ranked);trade,_=simulate(candidate_ranked,candidate["threshold"])
        candidate_rows.append({"target":candidate["target_label"],"feature_set":candidate["feature_set"],"feature_count":len(candidate_model.features),"threshold":candidate["threshold"],**metrics,"net_return":trade["net_return"],"trades":trade["trades"]})
    random_rank,random_trade=random_reference(test);baselines={"cash":{"net_return":0.,"pnl":0.},"always_btc":simulate(ranked,-np.inf,"btc")[0],"momentum":simulate(ranked,-np.inf,"momentum")[0],"random_eligible":random_trade,"oracle_impossible":simulate(ranked,-np.inf,"oracle")[0]}
    v1_path=PROJECT_ROOT/"data/models/opportunity_ranker_v1";v1_meta=json.loads((v1_path/"metadata.json").read_text());v1=OpportunityRanker.load(v1_path/"model.joblib");v1_ranked=ranked_predictions(test,v1.predict(test));baselines["opportunity_ranker_v1_on_v2_universe"]=simulate(v1_ranked,v1_meta["decision_threshold"])[0]
    distribution=decision_distribution(ranked,threshold);selected=distribution[distribution.selection!="NO_TRADE"];total=int(selected["count"].sum());largest=float(selected["count"].max()/total) if total else 0.;collapse={"flagged":largest>.5,"largest_symbol_share":largest,"threshold":.5}
    symbols=per_symbol_results(ledger,sorted(test.symbol.unique()));actual=ranked[ranked.future_return_rank==1].symbol.value_counts();predicted=ranked[ranked.predicted_rank==1].symbol.value_counts();
    symbols["median_return"]=[float(ledger.loc[ledger.symbol==s,"actual_net_return"].median()) if (ledger.symbol==s).any() else 0 for s in symbols.symbol];symbols["actual_best_frequency"]=[int(actual.get(s,0)) for s in symbols.symbol];symbols["predicted_best_frequency"]=[int(predicted.get(s,0)) for s in symbols.symbol]
    profit_factors=[]
    for symbol in symbols.symbol:
        pnl=ledger.loc[ledger.symbol==symbol,"pnl"] if not ledger.empty else pd.Series(dtype=float);wins=pnl[pnl>0].sum();losses=pnl[pnl<=0].sum()
        profit_factors.append(float(wins/abs(losses)) if losses else (float("inf") if wins else 0.))
    symbols["profit_factor"]=profit_factors
    volatility_cut=float(validation.drop_duplicates("timestamp").btc_volatility.median());regimes=regime_analysis(ranked,threshold,volatility_cut);gaps=score_gap_analysis(ranked);strength=opportunity_strength(ranked,validation);walk=walk_forward(frame,meta["parameters"])
    errors=[]
    for timestamp,group in ranked.groupby("timestamp"):
        ordered=group.sort_values("predicted_rank");winner=ordered.iloc[0];reasons=[];chosen=winner.predicted_net_return>=threshold
        if winner.future_return_rank>len(group)/2:reasons.append("predicted_top_actual_bottom_half")
        if chosen and winner.future_net_return_4h<0 and group.future_net_return_4h.max()>=.01:reasons.append("loser_while_strong_alternative")
        if not chosen and group.future_net_return_4h.max()>=.01:reasons.append("no_trade_missed_strong")
        if chosen and (group.future_net_return_4h<0).all():reasons.append("trade_all_negative")
        if winner.future_return_rank==len(group):reasons.append("severe_score_inversion")
        if reasons:errors.append({"timestamp":timestamp,"universe":",".join(ordered.symbol),"predicted_ranking":",".join(ordered.symbol),"actual_ranking":",".join(group.sort_values('future_return_rank').symbol),"predicted_values":json.dumps(ordered.predicted_net_return.tolist()),"future_returns":json.dumps(ordered.future_net_return_4h.tolist()),"reasons":";".join(reasons)})
    random_top=random_rank["analytical_top_1"];positive_folds=sum(x["net_return"]>0 for x in walk)
    if rank["top_1_accuracy"]>random_top+.05 and rank["average_spearman"]>.05 and trading["net_return"]>0 and positive_folds>=2 and not collapse["flagged"] and trading["trades"]>=100:status="PROMISING"
    elif rank["top_1_accuracy"]>random_top+.02 and rank["average_spearman"]>0 and (trading["net_return"]>0 or positive_folds>=2):status="WEAK"
    else:status="NO_SIGNAL"
    summary={"model_version":"opportunity_ranker_v2","research_only":True,"status":status,"status_criteria":{"PROMISING":"top1 > random+5pp, Spearman > .05, positive Test return, >=2 positive WF folds, no collapse, >=100 trades","WEAK":"top1 > random+2pp, positive Spearman, and positive Test or >=2 positive WF folds","otherwise":"NO_SIGNAL"},"target":meta["target_label"],"feature_set":meta["feature_set"],"feature_count":meta["feature_count"],"threshold":threshold,"validation_ranking":meta["validation_ranking"],"test_regression":regression,"test_ranking":rank,"random_reference":random_rank,"test_trading":trading,"baselines":baselines,"candidate_results":candidate_rows,"selection_distribution":distribution.to_dict("records"),"selection_collapse":collapse,"symbol_results":symbols.to_dict("records"),"regime_analysis":regimes,"score_gap_analysis":gaps,"opportunity_strength":strength,"walk_forward":walk,"error_counts":pd.Series([r for x in errors for r in x['reasons'].split(';')]).value_counts().to_dict(),"model_sha256":meta["model_sha256"]}
    pd.DataFrame(candidate_rows).to_csv(reports/"target_comparison.csv",index=False);pd.DataFrame(candidate_rows).to_csv(reports/"ablation.csv",index=False);ranked.to_csv(reports/"test_rankings.csv",index=False);distribution.to_csv(reports/"selection_distribution.csv",index=False);symbols.to_csv(reports/"symbol_results.csv",index=False);pd.DataFrame(regimes).to_csv(reports/"regime_analysis.csv",index=False);pd.DataFrame(gaps).to_csv(reports/"score_gap_analysis.csv",index=False);pd.DataFrame(walk).to_csv(reports/"walk_forward.csv",index=False);pd.DataFrame(errors).to_csv(reports/"errors.csv",index=False)
    (reports/"summary.json").write_text(json.dumps(clean(summary),indent=2,default=str),encoding="utf-8");(reports/"summary.md").write_text(f"# Opportunity Ranker v2\n\n- Research status: **{status}**\n- Winning target: {meta['target_label']}\n- Feature set: {meta['feature_set']}\n- Test top-1: {rank['top_1_accuracy']:.4f}\n- Random top-1: {random_top:.4f}\n- Test net return: {trading['net_return']:.4%}\n- Positive walk-forward folds: {positive_folds}/{len(walk)}\n",encoding="utf-8");meta["evaluation_summary"]=clean(summary);(registry/"metadata.json").write_text(json.dumps(clean(meta),indent=2),encoding="utf-8");return clean(summary)


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("--registry",default=str(DEFAULT_REGISTRY));p.add_argument("--dataset",default=str(DEFAULT_DATASET));p.add_argument("--metadata",default=str(DEFAULT_METADATA));p.add_argument("--reports",default=str(REPORTS));a=p.parse_args(argv);s=evaluate(a.registry,a.dataset,a.metadata,a.reports);print(json.dumps({"status":s["status"],"target":s["target"],"feature_set":s["feature_set"],"test_ranking":s["test_ranking"],"test_trading":s["test_trading"],"reports":str(Path(a.reports).resolve())},indent=2))


if __name__=="__main__":main()
